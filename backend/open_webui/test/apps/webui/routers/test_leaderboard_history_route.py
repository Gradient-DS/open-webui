from starlette.routing import Match

from open_webui.routers.evaluations import router


def test_history_route_matches_a_model_id_with_a_slash():
    """Catalog ids like ci/chat arrive decoded; without :path the SPA answered."""
    route = next(r for r in router.routes if r.path.endswith('/history'))
    match, child = route.matches({'type': 'http', 'method': 'GET', 'path': '/leaderboard/ci/chat/history'})
    assert match == Match.FULL
    assert child['path_params'] == {'model_id': 'ci/chat'}
