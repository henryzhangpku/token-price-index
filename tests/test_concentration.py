"""A seller count overstates how independent a fixing's inputs are.

``min_providers`` counts companies. It cannot see that most of them quote one
identical number -- fifteen of twenty-seven on the first live collection --
so a fixing that reads as broad is, in its central estimate, one publisher's
reference rate and fourteen sellers copying it. This is the token market's
form of the venue concentration the compute benchmark measures: there the
independence is lost through the route the price arrived by; here it is lost
through the price itself.

These tests pin the measurement and, just as importantly, pin that it stays a
*flag*. With a majority at one number on the live indices, any gate strict
enough to be meaningful would withhold everything, and any gate loose enough
to pass would have been chosen by looking at the sample it is meant to judge.
The number gets disclosed; the refusing waits for evidence.
"""

from __future__ import annotations

import pytest
from conftest import CODE

from tokidx.estimator import MODAL_SHARE_FLAG, estimate, modal_share
from tokidx.models import ProviderPoint
from tokidx.spec import DEFAULT_GATES, Tier


def _point(provider: str, price: float) -> ProviderPoint:
    return ProviderPoint(provider=provider, price=price, quote_count=1, best_tier=Tier.LIST)


def test_modal_share_is_the_fraction_at_the_commonest_price():
    points = [_point(p, 0.325) for p in "abcd"] + [_point("e", 0.30), _point("f", 0.40)]
    assert modal_share(points) == pytest.approx(4 / 6)


def test_every_seller_different_is_a_share_of_one_seller():
    points = [_point(p, 0.30 + i * 0.01) for i, p in enumerate("abcde")]
    assert modal_share(points) == pytest.approx(1 / 5)


def test_no_sellers_is_undefined_not_zero():
    """Zero would read as 'no concentration', which is the wrong answer for a
    market nobody is in."""
    assert modal_share([]) is None


def test_flag_fires_when_a_majority_quotes_one_number(market, day):
    f = estimate(CODE, day, market({"a": 3.0, "b": 3.0, "c": 3.0, "d": 3.1, "e": 2.9}), [],
                 DEFAULT_GATES)
    flags = [fl for fl in f.flags if fl.code == "price_concentration"]
    assert len(flags) == 1
    assert flags[0].severity == "warn"
    assert "3 of 5" in flags[0].detail


def test_flag_is_silent_at_exactly_the_threshold(market, day):
    """Half is the boundary, and the boundary is not a breach. The threshold's
    stated meaning is that *above* it the median is the modal price by
    construction; at exactly half it need not be, and a rule that fired there
    would flag every two-price market."""
    f = estimate(CODE, day, market({"a": 3.0, "b": 3.0, "c": 3.2, "d": 3.4}), [], DEFAULT_GATES)
    share = modal_share(f.contributing)
    assert share == MODAL_SHARE_FLAG
    assert not [fl for fl in f.flags if fl.code == "price_concentration"]


def test_flag_is_silent_on_a_diverse_sample(market, day):
    f = estimate(CODE, day, market({"a": 3.0, "b": 3.1, "c": 3.2, "d": 3.3}), [], DEFAULT_GATES)
    assert not [fl for fl in f.flags if fl.code == "price_concentration"]


def test_screened_sellers_do_not_count_toward_the_share(market, day):
    """A screened seller contributes no weight, so it cannot contribute
    concentration either. The share is measured over what was actually used."""
    f = estimate(
        CODE, day,
        market({"a": 3.0, "b": 3.0, "c": 3.0, "d": 3.1, "e": 2.9, "manipulator": 0.01}),
        [], DEFAULT_GATES,
    )
    assert [p.provider for p in f.providers if p.screened_out] == ["manipulator"]
    assert modal_share(f.contributing) == pytest.approx(3 / 5)


def test_concentration_never_becomes_a_gate(market, day):
    """The whole point: disclosed, not enforced. A fixing whose sellers all
    quote one number must still publish if the real gates hold."""
    f = estimate(CODE, day, market(dict.fromkeys("abcdef", 3.0)), [], DEFAULT_GATES)
    assert any(fl.code == "price_concentration" for fl in f.flags)
    assert "price_concentration" not in {g.name for g in f.gates}
    assert f.published, f.withheld_reason
    assert f.value == pytest.approx(3.0)
