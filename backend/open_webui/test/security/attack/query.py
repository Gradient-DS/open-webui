"""Drive spec-derived string queries together and individually.

RUN-TAG: owui-phase7b-remaining-passes

The committed surface derives 47 operations / 94 string query parameters.
Pair-dependent handlers need the all-at-once request; individual requests
attribute failures to each parameter. Requests encodes params, preserving
hostile &, #, ?, Unicode and embedded newlines as values. The core's SHA-256
per-field sampling is stable; ATTACK_FULL_CORPUS=1 visits every payload.
Writes preserve configuration, deletes follow reads, and declared destructive
operations use the core's fresh throwaway identity on every request.
"""

import os

from hostile_corpus import fetch_payloads
from openapi_surface import query_parameters

from . import plane
from .pass_support import pass_run
from .seeds import SPEC


def query_targets(spec=SPEC):
    return {route: names for route, names in query_parameters(spec).items() if names}


def drive_query_parameters(admin, parameters, *, spec=SPEC, payloads=None, full=None):
    targets = query_targets(spec)
    payloads = tuple(fetch_payloads() if payloads is None else payloads)
    full = os.getenv('ATTACK_FULL_CORPUS') == '1' if full is None else full
    with pass_run('query', targets, admin) as tally:
        for route in sorted(targets, key=plane._read_before_destroy):
            target = plane._target(route, parameters, tally)
            if target is None:
                continue
            names = targets[route]
            for batch in plane._payload_batches(route, names, payloads, full=full):
                plane._drive_one(admin, route, target, 'query', params=batch)
                if len(names) > 1:
                    for name in names:
                        plane._drive_one(admin, route, target, 'query', params={name: batch[name]})
            plane.flush_hits()
    return tally
