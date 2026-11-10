from open_webui.routers.evaluations import router


def test_retired_leaderboard_history_has_no_route():
    assert not any(route.path.endswith('/history') for route in router.routes)
