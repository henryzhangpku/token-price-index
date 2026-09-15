"""Restatement: every observation becomes the contract, or is discarded with a reason.

This is the step most open to argument, so every quote keeps the factors that
were applied and the raw number they were applied to. These tests pin that the
right inputs pass untouched, the wrong ones are refused for the stated reason,
and the factors compose the way the methodology says they do.
"""

from __future__ import annotations

import pytest
from conftest import CODE

from tokidx.models import Quote, Rejection
from tokidx.normalize import normalize, normalize_all, serving_check, serving_mix
from tokidx.spec import CONTRACTS, MAX_TOTAL_ADJUSTMENT, SERVING_FACTORS, Direction, Serving

CONTRACT = CONTRACTS[CODE]


def test_conforming_quote_passes_through_unadjusted(make_obs):
    q = normalize(make_obs(price=0.50), CONTRACT)
    assert isinstance(q, Quote)
    assert q.usd_per_mtok == 0.50
    assert q.raw_usd_per_mtok == 0.50
    assert q.adjustments == []
    assert q.total_adjustment == 1.0


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"model": "moonshotai/kimi-k3"}, "model_mismatch"),
        ({"direction": Direction.INPUT}, "direction_mismatch"),
        ({"region": "eu"}, "region_mismatch"),
        ({"price": 0.0}, "non_positive_price"),
        ({"price": -1.0}, "non_positive_price"),
    ],
)
def test_wrong_good_is_rejected_with_its_reason(make_obs, override, reason):
    r = normalize(make_obs(**override), CONTRACT)
    assert isinstance(r, Rejection)
    assert r.reason == reason
    assert r.provider == "seller-a"


def test_model_match_is_exact_not_fuzzy(make_obs):
    """A newer or older revision of the same family is a different good.
    Absorbing it would average two products under one name."""
    r = normalize(make_obs(model=CONTRACT.model + "-v2"), CONTRACT)
    assert isinstance(r, Rejection)
    assert r.reason == "model_mismatch"


def test_batch_is_restated_up_to_standard(make_obs):
    q = normalize(make_obs(price=0.25, serving=Serving.BATCH), CONTRACT)
    assert isinstance(q, Quote)
    assert q.usd_per_mtok == pytest.approx(0.25 * SERVING_FACTORS[Serving.BATCH])
    assert [a.factor_name for a in q.adjustments] == ["serving"]
    assert q.adjustments[0].factor == SERVING_FACTORS[Serving.BATCH]


def test_priority_is_restated_down_to_standard(make_obs):
    q = normalize(make_obs(price=1.00, serving=Serving.PRIORITY), CONTRACT)
    assert isinstance(q, Quote)
    assert q.usd_per_mtok == pytest.approx(0.50)


def test_a_cache_read_is_over_adjusted_and_discarded(make_obs):
    """Restating a cache read multiplies by ten. Nine tenths of that number
    would be the schedule talking, not the seller, so it is refused. This is
    the case the 5x ceiling exists to bind on."""
    assert SERVING_FACTORS[Serving.CACHED] > MAX_TOTAL_ADJUSTMENT
    r = normalize(make_obs(price=0.05, serving=Serving.CACHED), CONTRACT)
    assert isinstance(r, Rejection)
    assert r.reason == "over_adjusted"
    assert f"{MAX_TOTAL_ADJUSTMENT}x" in r.detail


@pytest.mark.parametrize("context", [128_000, 256_000, 1_048_576])
def test_a_flat_rate_covering_the_benchmark_window_is_the_benchmark_good(make_obs, context):
    """The window is what a single rate covers, not a premium tier. A 128k
    request is served at that rate, so there is nothing to restate.

    An earlier draft multiplied these by 0.80 or 0.65 and every published
    value came out a third below what any seller charged. Pinned so the
    factor cannot come back without a seller actually publishing a tier."""
    q = normalize(make_obs(price=1.00, context_tokens=context), CONTRACT)
    assert isinstance(q, Quote)
    assert q.adjustments == []
    assert q.usd_per_mtok == 1.00


def test_a_window_too_short_for_the_benchmark_is_a_different_good(make_obs):
    """A 32k window cannot serve a 128k request. That is not a cheaper price
    for the good; it is not the good."""
    r = normalize(make_obs(price=0.10, context_tokens=32_000), CONTRACT)
    assert isinstance(r, Rejection)
    assert r.reason == "context_too_short"
    assert "32,000" in r.detail


def test_the_serving_factor_is_recorded_so_it_can_be_replayed(make_obs):
    q = normalize(make_obs(price=1.00, serving=Serving.BATCH, context_tokens=1_000_000), CONTRACT)
    assert isinstance(q, Quote)
    assert [a.factor_name for a in q.adjustments] == ["serving"]
    assert q.total_adjustment == pytest.approx(2.0)
    assert q.adjustments[0].reason == "batch restated to standard"


def test_the_ceiling_is_reachable():
    """A ceiling nothing can touch is not a control. An earlier draft set it
    at 10.5x, above every combination of factors; a test like this one
    caught it. The largest single factor must be able to breach it."""
    assert max(SERVING_FACTORS.values()) > MAX_TOTAL_ADJUSTMENT


def test_normalize_all_accounts_for_every_observation(make_obs):
    observations = [
        make_obs(provider="a", price=0.5),
        make_obs(provider="b", price=0.6, serving=Serving.BATCH),
        make_obs(provider="c", price=0.7, model="other/model"),
        make_obs(provider="d", price=0.05, serving=Serving.CACHED),
    ]
    quotes, rejections = normalize_all(observations, CODE)
    assert len(quotes) + len(rejections) == len(observations)
    assert sorted(q.provider for q in quotes) == ["a", "b"]
    assert sorted(r.reason for r in rejections) == ["model_mismatch", "over_adjusted"]


def test_serving_check_reads_a_sellers_own_ratio(make_obs):
    """One seller publishing the same good two ways is the only calibration
    the serving schedule has. The observed ratio is evidence."""
    observations = [
        make_obs(provider="a", price=1.00, serving=Serving.STANDARD),
        make_obs(provider="a", price=0.50, serving=Serving.BATCH),
        make_obs(provider="b", price=0.80, serving=Serving.STANDARD),  # nothing to compare
    ]
    evidence = serving_check(observations)
    assert len(evidence) == 1
    provider, good, ratio = evidence[0]
    assert provider == "a"
    assert good.endswith("batch")
    assert ratio == pytest.approx(2.0)


def test_serving_mix_distinguishes_a_one_sided_market_from_a_one_sided_feed(make_obs):
    """`serving_check` returning nothing has two causes and they are not the same.

    Every seller might genuinely publish one way, or the SOURCE might report
    one way. Only the second makes the schedule inert rather than merely
    unvalidated, and a reader cannot tell them apart from an empty result.
    `serving_mix` is the count that distinguishes them, and it is what
    `tokidx calibrate` prints instead of asserting the conclusion.
    """
    one_sided_feed = [
        make_obs(provider="a", serving=Serving.STANDARD),
        make_obs(provider="b", serving=Serving.STANDARD),
        make_obs(provider="c", serving=Serving.STANDARD),
    ]
    assert serving_check(one_sided_feed) == []
    assert serving_mix(one_sided_feed) == {"standard": 3}

    # Same empty evidence, entirely different situation: two modes are present,
    # they just never meet at one seller.
    split = [
        make_obs(provider="a", serving=Serving.STANDARD),
        make_obs(provider="b", serving=Serving.BATCH),
    ]
    assert serving_check(split) == []
    assert serving_mix(split) == {"batch": 1, "standard": 1}

    # Largest first, so the dominant mode reads off the top row.
    skewed = [make_obs(provider=f"p{i}", serving=Serving.STANDARD) for i in range(4)]
    skewed.append(make_obs(provider="x", serving=Serving.BATCH))
    assert list(serving_mix(skewed)) == ["standard", "batch"]
    assert serving_mix([]) == {}


def test_the_cache_factor_is_an_exclusion_not_an_adjustment():
    """10x against a 5x ceiling means a cached input can never be admitted.

    That is deliberate -- nine tenths of the restated number would come from
    the schedule rather than the seller -- but it makes `cached` an exclusion
    rule wearing a factor's clothes, and `calibrate` now says so rather than
    listing it beside factors that could actually fire.
    """
    assert SERVING_FACTORS[Serving.CACHED] > MAX_TOTAL_ADJUSTMENT
    assert SERVING_FACTORS[Serving.BATCH] <= MAX_TOTAL_ADJUSTMENT
    assert SERVING_FACTORS[Serving.STANDARD] == 1.00
