"""Estimator behaviour under ordinary and adversarial inputs.

The manipulation tests are the point of this file. An index somebody could
settle against has to survive a seller who wants the print somewhere else,
and every defence in the estimator answers one specific move: flooding the
venue, quoting an absurd number, holding a privileged evidence tier, or
exploiting a market that agrees so exactly that the usual screen is blind.
"""

from __future__ import annotations

import pytest
from conftest import CODE

from tokidx.estimator import (
    DEGENERATE_RATIO,
    collapse_to_providers,
    estimate,
    screen,
)
from tokidx.models import ProviderPoint
from tokidx.spec import DEFAULT_GATES, Tier

GATES = DEFAULT_GATES


def _fix(quotes, day, gates=GATES):
    return estimate(CODE, day, quotes, [], gates)


def _point(provider: str, price: float, tier: Tier = Tier.LIST) -> ProviderPoint:
    return ProviderPoint(provider=provider, price=price, quote_count=3, best_tier=tier)


# -- the ordinary case ------------------------------------------------------


def test_value_is_the_weighted_mean_of_seller_medians(market, day):
    f = _fix(market({"a": 2.0, "b": 3.0, "c": 4.0, "d": 5.0}), day)
    assert f.published, f.withheld_reason
    assert f.value == pytest.approx(3.5)


def test_one_seller_gets_one_vote_regardless_of_catalogue_size(make_obs, market, day):
    """A seller listing the same weights forty ways must not outvote the market."""
    from tokidx.normalize import normalize
    from tokidx.spec import CONTRACTS

    honest = market({"a": 3.0, "b": 3.0, "c": 3.0, "d": 3.0})
    flooder = [
        normalize(make_obs(provider="flooder", price=1.0, note=f"sku-{i}"), CONTRACTS[CODE])
        for i in range(40)
    ]

    points = collapse_to_providers(honest + flooder)
    assert next(p for p in points if p.provider == "flooder").quote_count == 40
    assert len(points) == 5

    f = _fix(honest + flooder, day)
    # Uncollapsed, forty rows at $1 against twelve at $3 would print near $1.5.
    assert f.value is not None and f.value > 2.0


def test_single_absurd_offer_is_screened_by_mad(market, day):
    f = _fix(market({"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.05, "manipulator": 0.05}), day)
    assert [p.provider for p in f.providers if p.screened_out] == ["manipulator"]
    assert f.value == pytest.approx(3.0125, abs=0.02)
    assert [fl.code for fl in f.flags if fl.code.startswith("outlier")] == ["outlier_screened"]


def test_a_rate_card_outweighs_the_same_price_seen_through_a_router(make_obs, day):
    """Same good, but the tier-1 seller pulls the value toward itself."""
    from tokidx.normalize import normalize
    from tokidx.spec import CONTRACTS

    contract = CONTRACTS[CODE]
    quotes = [normalize(make_obs(provider="card", price=2.0, tier=Tier.LIST), contract)]
    quotes += [
        normalize(make_obs(provider=p, price=4.0, tier=Tier.AGGREGATED), contract)
        for p in ("r1", "r2", "r3")
    ]
    f = _fix(quotes, day)
    assert f.published, f.withheld_reason
    # 1.0 x 2.0 + 3 x 0.6 x 4.0, over 2.8. A plain mean would say 3.5.
    assert f.value == pytest.approx(9.2 / 2.8, abs=1e-4)
    assert f.value < 3.5


def test_no_single_seller_exceeds_the_weight_cap(make_obs, day):
    """A tier-1 seller among curated entries must not carry the fixing alone."""
    from tokidx.normalize import normalize
    from tokidx.spec import CONTRACTS

    contract = CONTRACTS[CODE]
    quotes = [normalize(make_obs(provider="card", price=3.0, tier=Tier.LIST), contract)]
    quotes += [
        normalize(make_obs(provider=p, price=3.0, tier=Tier.CURATED), contract)
        for p in ("c1", "c2")
    ]
    f = _fix(quotes, day)

    total = sum(p.weight for p in f.contributing)
    for p in f.contributing:
        assert p.weight / total <= GATES.max_provider_weight_share + 1e-9
    assert any(fl.code == "provider_weight_capped" for fl in f.flags)


# -- refusal ----------------------------------------------------------------


def test_thin_market_is_withheld_not_guessed(market, day):
    """Two sellers is not a market. Unlike the compute benchmark, no candidate
    value is carried on a withheld fixing here: a number that failed its gates
    is not a number, and printing one invites it to be used."""
    f = _fix(market({"a": 3.0, "b": 3.1}), day)
    assert not f.published
    assert f.value is None
    assert "min_providers" in (f.withheld_reason or "")


def test_incoherent_market_is_withheld_on_dispersion(market, day):
    """Sellers this far apart are not pricing one good, and an average of
    them is a number with no market behind it."""
    f = _fix(market({"a": 1.0, "b": 1.5, "c": 2.5, "d": 4.0}), day)
    assert not [p for p in f.providers if p.screened_out], "nothing should be screened"
    assert not f.published
    gate = next(g for g in f.gates if g.name == "dispersion")
    assert not gate.passed
    assert f.dispersion is not None and f.dispersion > GATES.max_dispersion


def test_empty_input_withholds_without_crashing(day):
    f = _fix([], day)
    assert not f.published
    assert f.value is None
    assert f.dispersion is None
    assert {g.name for g in f.gates if not g.passed} >= {"min_providers", "dispersion"}


# -- the degenerate screen --------------------------------------------------
#
# Fifteen of twenty-seven sellers quoting one number is the live shape of this
# market, so MAD is zero more often than not and the sigma test is undefined
# exactly when an absurd quote does the most damage. The ratio fallback is
# what stands in for it, and these pin its edges.


def test_extreme_quote_is_screened_even_when_the_market_agrees_exactly(market, day):
    f = _fix(market({"a": 3.0, "b": 3.0, "c": 3.0, "d": 3.0, "manipulator": 0.05}), day)
    assert [p.provider for p in f.providers if p.screened_out] == ["manipulator"]
    assert [fl.code for fl in f.flags if fl.code.startswith("outlier")] == [
        "outlier_screened_degenerate"
    ]
    assert f.value == pytest.approx(3.0)


def test_exact_consensus_alone_screens_nobody(market, day):
    """The fallback must not start rejecting a market that simply agrees."""
    f = _fix(market({"a": 3.0, "b": 3.0, "c": 3.0, "d": 3.0}), day)
    assert not [p for p in f.providers if p.screened_out]
    assert f.published, f.withheld_reason
    assert f.value == pytest.approx(3.0)


def test_genuinely_cheap_capacity_survives_an_exact_consensus(market, day):
    """Half the consensus is market structure -- a seller with cheaper
    hardware or thinner margin -- not manipulation, and must be kept."""
    f = _fix(market({"a": 3.0, "b": 3.0, "c": 3.0, "d": 3.0, "cheap": 1.5}), day)
    assert not [p for p in f.providers if p.screened_out]


@pytest.mark.parametrize("price", [30.0, 0.3])
def test_degenerate_screen_is_symmetric(market, day, price):
    """Ten times the consensus and a tenth of it are the same distance away.
    A percentage test would see +900% on one side and -90% on the other."""
    f = _fix(market({"a": 3.0, "b": 3.0, "c": 3.0, "d": 3.0, "far": price}), day)
    assert [p.provider for p in f.providers if p.screened_out] == ["far"]


def test_degenerate_screen_handles_a_zero_median():
    """A zero consensus would divide by zero in the ratio. It returns nothing
    rather than raising; the gates refuse such a market on their own."""
    points = [_point(p, 0.0) for p in ("a", "b", "c")]
    assert screen(points) == []
    assert not any(p.screened_out for p in points)


@pytest.mark.parametrize("level", [0.01, 1.0, 100.0])
def test_the_degenerate_band_is_scale_invariant_at_its_boundary(level):
    """A seller exactly at the ratio is kept whatever the price level. The
    boundary is not a breach, and a band that leaked at one scale would be a
    different rule at each price."""
    points = [_point(p, level) for p in ("a", "b", "c", "d")]
    points.append(_point("edge", level * DEGENERATE_RATIO))
    screen(points)
    assert not any(p.screened_out for p in points)


def test_a_seller_past_the_degenerate_band_is_still_screened():
    """The epsilon must not blunt the screen it protects."""
    points = [_point(p, 1.0) for p in ("a", "b", "c", "d")]
    points.append(_point("over", DEGENERATE_RATIO * (1 + 1e-6)))
    screen(points)
    assert [p.provider for p in points if p.screened_out] == ["over"]


def test_screen_needs_three_live_sellers():
    """Below three there is no consensus to measure distance from."""
    points = [_point("a", 1.0), _point("b", 100.0)]
    assert screen(points) == []
    assert not any(p.screened_out for p in points)
