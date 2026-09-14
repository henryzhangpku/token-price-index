"""Checks for failures that arrive as well-formed data.

A source returning last month's prices forever, a seller quietly vanishing,
or one seller repricing by a factor of four overnight all arrive as valid
numbers and would otherwise flow straight into a published value.

Two things are pinned here beyond the arithmetic. First, that every check
reports ``not_evaluable`` when it has nothing to compare against, rather than
passing on no evidence. Second, the attack the estimator cannot see: a seller
moving its own price far enough to drag the fixing, while staying inside the
outlier band because the median moved with it. Only a comparison against that
seller's own previous price catches it, and it is flagged, not gated.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from tokidx.estimator import screen
from tokidx.models import ProviderPoint
from tokidx.quality import (
    INDEX_LEVEL_SHIFT,
    MAX_STALENESS_DAYS,
    NOT_EVALUABLE,
    PROVIDER_LEVEL_SHIFT,
    CheckResult,
    check_index_level_shift,
    check_provider_dropout,
    check_provider_level_shift,
    check_staleness,
    run_checks,
    since,
)
from tokidx.spec import Tier

DAY = date(2026, 9, 8)

#: A twelve-seller panel with a real spread, so the robust sigma is wide
#: enough for a large move to land inside the keep band. That is the attack:
#: the outlier screen measures distance from the panel, and a seller that
#: reprices toward the panel's far side never stands out from it.
PANEL = {
    "a": 0.24, "b": 0.27, "c": 0.30, "d": 0.32, "e": 0.33, "f": 0.34,
    "g": 0.36, "h": 0.38, "i": 0.42, "j": 0.46, "k": 0.52, "attacker": 0.50,
}


def _at(day: date, hour: int = 12) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


# -- staleness ---------------------------------------------------------------


def test_fresh_prices_pass():
    r = check_staleness(_at(DAY), DAY)
    assert r.passed


def test_prices_older_than_the_ceiling_fail():
    r = check_staleness(_at(DAY - timedelta(days=MAX_STALENESS_DAYS + 1)), DAY)
    assert r.status == "fail"
    assert str(MAX_STALENESS_DAYS) in r.detail


def test_prices_from_the_future_fail():
    """A fixing dated before its inputs were read is not stale, it is wrong."""
    r = check_staleness(_at(DAY + timedelta(days=1)), DAY)
    assert r.status == "fail"
    assert "future" in r.detail


# -- dropout -----------------------------------------------------------------


def test_dropout_is_not_evaluable_with_no_history():
    r = check_provider_dropout({}, {"a": 1.0})
    assert r.status == NOT_EVALUABLE
    assert not r.evaluable


def test_a_vanished_seller_is_named():
    r = check_provider_dropout({"a": 1.0, "b": 1.0, "c": 1.0}, {"a": 1.0, "c": 1.0})
    assert r.status == "fail"
    assert "b" in r.detail


def test_a_new_seller_is_not_a_dropout():
    r = check_provider_dropout({"a": 1.0}, {"a": 1.0, "b": 1.0})
    assert r.passed


# -- index level shift -------------------------------------------------------


@pytest.mark.parametrize(("previous", "current"), [(None, 1.0), (1.0, None), (0.0, 1.0)])
def test_index_shift_is_not_evaluable_without_two_positive_values(previous, current):
    assert check_index_level_shift(previous, current).status == NOT_EVALUABLE


@pytest.mark.parametrize("direction", [1, -1])
def test_index_shift_threshold_is_symmetric(direction):
    inside = 1.0 * (1 + direction * (INDEX_LEVEL_SHIFT - 0.01))
    outside = 1.0 * (1 + direction * (INDEX_LEVEL_SHIFT + 0.01))
    assert check_index_level_shift(1.0, inside).passed
    assert check_index_level_shift(1.0, outside).status == "fail"


# -- provider level shift: the attack ---------------------------------------


def test_the_attack_is_flagged():
    moved = dict(PANEL, attacker=PANEL["attacker"] * 0.5)
    r = check_provider_level_shift(PANEL, moved)
    assert r.status == "fail"
    assert "attacker" in r.detail
    assert "-50.0%" in r.detail


def test_the_outlier_screen_does_not_catch_it():
    """The point of having this check at all. A 50% reprice is a breach of
    the level-shift threshold twice over, and the estimator's own defence
    stays silent because the attacker lands inside the keep band."""
    points = [
        ProviderPoint(p, PANEL["attacker"] * 0.5 if p == "attacker" else v, 3, Tier.LIST)
        for p, v in PANEL.items()
    ]
    screen(points)
    attacker = next(p for p in points if p.provider == "attacker")
    assert not attacker.screened_out, attacker.screen_reason
    # And the move is well past what the level-shift check tolerates, so the
    # two defences genuinely disagree about the same input.
    assert abs(0.5 - 1.0) > PROVIDER_LEVEL_SHIFT


def test_an_ordinary_reprice_is_not_flagged():
    moved = dict(PANEL, h=PANEL["h"] * 1.10)
    r = check_provider_level_shift(PANEL, moved)
    assert r.passed, r.detail


@pytest.mark.parametrize("direction", [1, -1])
def test_provider_shift_threshold_is_symmetric(direction):
    inside = dict(PANEL, h=PANEL["h"] * (1 + direction * (PROVIDER_LEVEL_SHIFT - 0.01)))
    outside = dict(PANEL, h=PANEL["h"] * (1 + direction * (PROVIDER_LEVEL_SHIFT + 0.01)))
    assert check_provider_level_shift(PANEL, inside).passed
    assert check_provider_level_shift(PANEL, outside).status == "fail"


def test_a_new_seller_raises_nothing():
    """No previous price, no move to measure."""
    r = check_provider_level_shift(PANEL, dict(PANEL, newcomer=0.01))
    assert r.passed


def test_no_previous_fixing_is_not_evaluable():
    assert check_provider_level_shift({}, PANEL).status == NOT_EVALUABLE


def test_no_overlap_is_not_evaluable():
    r = check_provider_level_shift({"x": 1.0}, {"y": 1.0})
    assert r.status == NOT_EVALUABLE


# -- the answers are flags, and 'could not look' is an answer ---------------


def test_a_failure_becomes_a_warning_flag_not_a_gate():
    flag = CheckResult("provider_level_shift", "fail", "attacker -50.0%").as_flag()
    assert flag is not None
    assert flag.severity == "warn"


def test_not_evaluable_is_disclosed_as_info():
    flag = CheckResult("provider_dropout", NOT_EVALUABLE, "no previous fixing").as_flag()
    assert flag is not None
    assert flag.severity == "info"


def test_a_pass_is_silent():
    assert CheckResult("staleness", "pass", "fresh").as_flag() is None


def test_run_checks_returns_every_check_in_reading_order():
    results = run_checks(
        collected_at=_at(DAY), index_date=DAY,
        previous_prices={}, current_prices=PANEL,
        previous_value=None, current_value=0.33,
    )
    assert [r.code for r in results] == [
        "staleness", "provider_dropout", "provider_level_shift", "index_level_shift",
    ]
    assert results[0].passed
    assert all(r.status == NOT_EVALUABLE for r in results[1:])


def test_since_is_the_previous_day_by_default():
    assert since(DAY) == DAY - timedelta(days=1)
    assert since(DAY, 7) == DAY - timedelta(days=7)
