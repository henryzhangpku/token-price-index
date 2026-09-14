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
from tokidx.normalize import normalize, normalize_all, serving_check
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


@pytest.mark.parametrize(
    ("context", "factor"),
    [
        (64_000, None),        # shorter than benchmark: not priced down
        (128_000, None),       # the benchmark itself
        (256_000, 0.80),       # up to 4x: the first step
        (512_000, 0.80),
        (1_000_000, 0.65),     # beyond 4x: the second step
    ],
)
def test_long_context_is_a_step_not_a_curve(make_obs, context, factor):
    q = normalize(make_obs(price=1.00, context_tokens=context), CONTRACT)
    assert isinstance(q, Quote)
    if factor is None:
        assert q.adjustments == []
        assert q.usd_per_mtok == 1.00
    else:
        assert [a.factor_name for a in q.adjustments] == ["context"]
        assert q.usd_per_mtok == pytest.approx(factor)


def test_adjustments_compose_multiplicatively(make_obs):
    """Batch at 1M context: x2.0 for serving, x0.65 for context, in that
    order, and the quote records both so the arithmetic can be replayed."""
    q = normalize(make_obs(price=1.00, serving=Serving.BATCH, context_tokens=1_000_000), CONTRACT)
    assert isinstance(q, Quote)
    assert [a.factor_name for a in q.adjustments] == ["serving", "context"]
    assert q.total_adjustment == pytest.approx(2.0 * 0.65)
    assert q.usd_per_mtok == pytest.approx(1.30)


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
