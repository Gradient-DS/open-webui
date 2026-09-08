"""Probe non-object JSON and whitespace around live, syntactically valid IDs.

RUN-TAG: owui-phase7b-remaining-passes

Applicability: FastAPI/Pydantic v2 rejects every NON_OBJECT_BODIES entry at
object-typed dependencies, including falsy [] and null. Its Rust pattern
validator and UUID parser reject a trailing newline too. The sibling's
SpecTree ordering bypass therefore does not transfer to those dependencies.
Plain string fields still accept whitespace, Python re's $ still accepts a
final newline, and a raw Request with no body dependency has no structural
guard. Offline HTTP controls pin these distinctions; live results determine
which application handlers are affected. Keep this inexpensive regression.

Every non-destructive write is selected, including routes with no declared
body or derived fields. NON_OBJECT_BODIES is always complete; whitespace
uses the engine's deterministic per-field sample, or every variant with
ATTACK_FULL_CORPUS=1. Each field is also sent alone so sibling validation
cannot hide the scalar alternative. Reads and destructive identities are
outside this pass. Configuration is restored after each write and the pass.
"""

import json
import os
from uuid import UUID

from hostile_corpus import NON_OBJECT_BODIES, whitespace_variants
from openapi_surface import writable_string_fields

from . import plane
from .pass_support import pass_run
from .seeds import DESTRUCTIVE, SPEC


def write_operations(spec=SPEC):
    return [
        route
        for route in plane.operations(spec)
        if route.split(' ', 1)[0] in {'POST', 'PUT', 'PATCH', 'DELETE'} and route not in DESTRUCTIVE
    ]


def valid_id(parameters):
    for value in parameters.values():
        try:
            UUID(value)
            return value
        except ValueError:
            continue
    raise ValueError('Whitespace probes require a live seeded UUID')


def drive_shapes(admin, parameters, *, spec=SPEC, full=None):
    routes = write_operations(spec)
    fields = writable_string_fields(spec)
    variants = whitespace_variants(valid_id(parameters)) if any(fields.get(r) for r in routes) else ()
    full = os.getenv('ATTACK_FULL_CORPUS') == '1' if full is None else full
    with pass_run('shapes', routes, admin) as tally:
        for route in routes:
            target = plane._target(route, parameters, tally)
            if target is None:
                continue
            for body in NON_OBJECT_BODIES:
                # requests' json=None sends no body. Send the literal JSON null.
                plane._drive_one(
                    admin, route, target, 'shapes', data=json.dumps(body), headers={'Content-Type': 'application/json'}
                )
            names = fields.get(route, [])
            if names:
                for batch in plane._payload_batches(route, names, variants, full=full):
                    plane._drive_one(admin, route, target, 'shapes', json=plane._body_for(names, batch))
                    if len(names) > 1:
                        for name in names:
                            plane._drive_one(admin, route, target, 'shapes', json=plane._body_for([name], batch))
            plane.flush_hits()
    return tally
