"""Coverage gates run after all live attack passes in the same process."""

from .test_route_coverage import (
    assert_every_route_is_driven_or_waived,
    assert_no_waiver_covers_a_route_the_plane_actually_reached,
)


def test_every_route_is_driven_or_waived():
    assert_every_route_is_driven_or_waived()


def test_no_waiver_covers_a_route_the_plane_actually_reached():
    assert_no_waiver_covers_a_route_the_plane_actually_reached()
