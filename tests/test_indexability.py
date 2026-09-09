"""The claim this repository exists to make, pinned as tests.

A benchmark requires a good with more than one seller. Where a good has one
seller there is nothing to discover, and an index over it reports a private
pricing decision under a public name. That is a methodology position, not a
data-sufficiency problem, so it is gated separately and evaluated first.

If a future change ever lets a single-seller good publish, these fail.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from tokidx.estimator import collapse_to_providers, dispersion, screen
from tokidx.models import Observation, ProviderPoint
from tokidx.normalize import normalize_all
from tokidx.pipeline import run, run_all
from tokidx.spec import CONTRACTS, DEFAULT_GATES, Direction, Serving, Tier

DAY = date(2026, 9, 8)


def _obs(provider: str, model: str, price: float, direction=Direction.OUTPUT,
         serving=Serving.STANDARD, tier=Tier.LIST) -> Observation:
    return Observation(
        provider=provider, model=model, direction=direction, usd_per_mtok=price,
        serving=serving, context_tokens=128_000, region="us", tier=tier,
        observed_at=datetime(2026, 9, 8, tzinfo=UTC), source_url="https://example.test",
    )


def test_a_single_seller_good_never_publishes() -> None:
    """The headline claim. Frontier models have one seller each."""
    f = run("TIX-FRONTIER-OUT", DAY)
    assert not f.published
    gate = next(g for g in f.gates if g.name == "indexable_good")
    assert not gate.passed
    assert "one seller" in gate.detail


def test_indexability_is_decided_before_data_sufficiency() -> None:
    """Even with abundant sellers, a proprietary good stays unindexable.

    This is the test that stops the position eroding: it would be easy for a
    later change to treat 'not indexable' as merely 'not enough data yet', and
    for the gate to quietly start passing once coverage improved.
    """
    quotes, rejections = normalize_all(
        [_obs(f"seller{i}", "frontier", 20.0 + i) for i in range(9)],
        "TIX-FRONTIER-OUT",
    )
    from tokidx.estimator import estimate

    f = estimate("TIX-FRONTIER-OUT", DAY, quotes, rejections, DEFAULT_GATES)

    assert len(f.contributing) == 9
    assert not next(g for g in f.gates if g.name == "indexable_good").passed
    assert not f.published, "nine sellers must not rescue an unindexable good"
    assert f.value is None


def test_an_open_weight_good_with_three_sellers_publishes() -> None:
    f = run("TIX-K26-OUT", DAY)
    assert f.published
    assert len(f.contributing) >= DEFAULT_GATES.min_providers
    assert f.value is not None


def test_the_published_value_sits_inside_the_observed_spread() -> None:
    f = run("TIX-K26-OUT", DAY)
    prices = [p.price for p in f.contributing]
    assert min(prices) <= f.value <= max(prices)


def test_input_and_output_are_separate_goods() -> None:
    """Blending them would require an assumed ratio nobody can observe."""
    out = run("TIX-K26-OUT", DAY)
    inp = run("TIX-K26-IN", DAY)
    assert out.value != inp.value
    assert CONTRACTS["TIX-K26-OUT"].direction is not CONTRACTS["TIX-K26-IN"].direction


def test_a_thin_panel_withholds_rather_than_printing() -> None:
    thin = [f for f in run_all(DAY).values()
            if not f.published
            and any(g.name == "min_providers" and not g.passed for g in f.gates)]
    assert thin, "the observation set should contain at least one thin index"
    assert all(f.value is None for f in thin)


def test_catalogue_size_buys_no_influence() -> None:
    """A seller listing the same good six ways still gets one vote."""
    one = collapse_to_providers(
        normalize_all([_obs("a", "kimi-k2.6", 4.0)], "TIX-K26-OUT")[0]
    )
    many = collapse_to_providers(
        normalize_all([_obs("a", "kimi-k2.6", 4.0) for _ in range(6)], "TIX-K26-OUT")[0]
    )
    assert len(one) == len(many) == 1
    assert one[0].price == many[0].price


def test_batch_restates_onto_the_sellers_own_standard_price() -> None:
    """The closest thing to a calibration check the schedule has.

    DeepInfra publishes standard and batch for the same model. Restating the
    batch price should land on the standard one, because the seller publishes
    batch at half.
    """
    quotes, _ = normalize_all(
        [_obs("deepinfra", "kimi-k2.6", 3.50),
         _obs("deepinfra", "kimi-k2.6", 1.75, serving=Serving.BATCH)],
        "TIX-K26-OUT",
    )
    assert len(quotes) == 2
    assert quotes[0].usd_per_mtok == pytest.approx(quotes[1].usd_per_mtok)


def test_a_cache_price_is_discarded_rather_than_multiplied_by_ten() -> None:
    """Nine tenths of a restated cache price would come from the schedule.

    The adjustment ceiling exists to stop the factors outvoting the seller.
    Batch at 2x survives because half the number is still theirs; a cache read
    at 10x does not.
    """
    quotes, rejections = normalize_all(
        [_obs("x", "kimi-k2.6", 0.10, direction=Direction.INPUT, serving=Serving.CACHED)],
        "TIX-K26-IN",
    )
    assert not quotes
    assert [r.reason for r in rejections] == ["over_adjusted"]


def test_a_batch_price_survives_the_ceiling() -> None:
    quotes, rejections = normalize_all(
        [_obs("x", "kimi-k2.6", 1.75, serving=Serving.BATCH)], "TIX-K26-OUT"
    )
    assert quotes and not rejections


def test_mad_zero_falls_back_to_a_ratio_band() -> None:
    """Exact agreement is where a sigma test breaks and one outlier does most harm."""
    points = [ProviderPoint(f"honest{i}", 3.00, 1, Tier.LIST) for i in range(4)]
    points.append(ProviderPoint("attacker", 1_000_000.0, 1, Tier.LIST))

    screen(points)

    assert [p.provider for p in points if p.screened_out] == ["attacker"]


def test_dispersion_is_undefined_rather_than_optimistic_below_three() -> None:
    assert dispersion([ProviderPoint("a", 1.0, 1, Tier.LIST),
                       ProviderPoint("b", 2.0, 1, Tier.LIST)]) is None


def test_undefined_dispersion_blocks_publication() -> None:
    """Not computable must never be treated as not disagreeing."""
    for f in run_all(DAY).values():
        if f.dispersion is None:
            assert not f.published
