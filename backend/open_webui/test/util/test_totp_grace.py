"""Unit tests for the 2FA enrollment grace-period computation.

These pin the fix for the bug where the grace period never expired: the
"days remaining" counter was a static config value and the "Set up later"
button was always available, so REQUIRE_2FA could be skipped forever and
every app restart appeared to reset the counter. The fix anchors the grace
window to a stored timestamp and computes a real deadline — which is exactly
what ``compute_twofa_grace`` does.
"""

from open_webui.utils.totp import compute_twofa_grace

DAY = 86400
ANCHOR = 1_700_000_000  # arbitrary fixed epoch second


def test_within_grace_not_expired():
    result = compute_twofa_grace(ANCHOR, 7, ANCHOR + 3 * DAY)
    assert result['deadline'] == ANCHOR + 7 * DAY
    assert result['expired'] is False


def test_past_grace_is_expired():
    result = compute_twofa_grace(ANCHOR, 7, ANCHOR + 8 * DAY)
    assert result['deadline'] == ANCHOR + 7 * DAY
    assert result['expired'] is True


def test_boundary_exact_deadline_is_expired():
    # At the exact deadline second the grace period is over.
    result = compute_twofa_grace(ANCHOR, 7, ANCHOR + 7 * DAY)
    assert result['expired'] is True


def test_deadline_is_stable_across_restarts():
    # Regression for the reported bug: the deadline must depend only on the
    # stored anchor, never on process/app start time — otherwise every
    # restart resets the window and 2FA can be deferred forever.
    early = compute_twofa_grace(ANCHOR, 7, ANCHOR + 1 * DAY)
    later = compute_twofa_grace(ANCHOR, 7, ANCHOR + 6 * DAY + 12345)
    assert early['deadline'] == later['deadline'] == ANCHOR + 7 * DAY


def test_unanchored_starts_fresh_window():
    # With no stored anchor the window starts at ``now`` and is not expired.
    now = ANCHOR
    result = compute_twofa_grace(None, 7, now)
    assert result['deadline'] == now + 7 * DAY
    assert result['expired'] is False


def test_zero_grace_period_expires_immediately():
    result = compute_twofa_grace(ANCHOR, 0, ANCHOR)
    assert result['deadline'] == ANCHOR
    assert result['expired'] is True


def test_negative_grace_period_treated_as_zero():
    result = compute_twofa_grace(ANCHOR, -5, ANCHOR)
    assert result['deadline'] == ANCHOR
    assert result['expired'] is True
