"""Lifecycle helpers for the additional passes; the Phase 7a engine owns evidence."""

from contextlib import contextmanager
from urllib.parse import quote

from . import plane, seeds
from .identities import ensure_identities


@contextmanager
def pass_run(name, routes, admin):
    tally = plane._PASSES[name] = plane.Seeding(expected=set(routes))
    try:
        with plane.preserve_configuration(
            admin,
            route=f'{name} pass',
            report=tally.config_findings.append,
            unverified=lambda reason: tally.config_unverified.setdefault(f'{name} pass', reason),
            durable=True,
        ):
            yield tally
        tally.config_restore_verified = not tally.config_unverified
    finally:
        plane.flush_hits()
        plane._report(name, tally)


@contextmanager
def fresh_surface():
    identities = ensure_identities()
    parameters = None
    try:
        parameters = seeds.resolve_parameters(identities.admin, admin=identities.admin)
        yield identities, parameters
    finally:
        try:
            if parameters is not None:
                task = parameters.get(('/api/tasks/stop/{task_id}', 'task_id'))
                if task:
                    response = identities.admin.request('POST', f'/api/tasks/stop/{quote(task, safe="")}')
                    response.close()
        finally:
            identities.close()


def crash_details(tally):
    return '\n'.join(
        f'{route}: HTTP { {s: n for s, n in tally.statuses[route].items() if s >= 500} }; body={body!r}'
        for route, body in sorted(tally.crashes.items())
    )
