"""Measuring how much of a fixing rests on judgement rather than observation."""

from __future__ import annotations

import pytest
from conftest import CODE

from tokidx.normalize import normalize
from tokidx.sensitivity import exposure
from tokidx.spec import CONTRACTS, DEFAULT_GATES, Serving

CONTRACT = CONTRACTS[CODE]


def _rows(make_obs, providers, price, **kwargs):
    return [
        normalize(make_obs(provider=p, price=price * tilt, **kwargs), CONTRACT)
        for p in providers
        for tilt in (1.0, 1.0, 1.0)
    ]


def test_a_fully_conforming_market_has_no_exposure(market, day):
    result = exposure(CODE, day, market({"a": 3.0, "b": 3.01, "c": 3.02, "d": 3.03}),
                      DEFAULT_GATES)
    assert result.conforming_quotes == result.total_quotes
    assert result.weight_share_adjusted == pytest.approx(0.0)
    assert result.shift == pytest.approx(0.0, abs=1e-9)
    assert result.publishable_without_adjustment


def test_exposure_counts_restated_quotes(make_obs, day):
    conforming = _rows(make_obs, ("a", "b"), 3.0)
    restated = _rows(make_obs, ("c", "d"), 1.5, serving=Serving.BATCH)  # x2 -> 3.0
    result = exposure(CODE, day, conforming + restated, DEFAULT_GATES)

    assert result.conforming_quotes == 6
    assert result.total_quotes == 12
    assert result.weight_share_adjusted == pytest.approx(0.5, abs=0.01)
    assert result.by_factor == {"serving": pytest.approx(0.5)}


def test_an_index_that_cannot_stand_without_restatement_is_reported_as_such(make_obs, day):
    """The case that matters: the schedule is load-bearing, not decorative.

    Only two sellers publish the benchmark configuration natively, so the
    conforming-only recomputation fails the seller gate and there is no
    counterfactual to compare against. That is a fact about the index worth
    stating, not an error.
    """
    conforming = _rows(make_obs, ("a", "b"), 3.0)
    restated = _rows(make_obs, ("c", "d", "e", "f"), 4.0, serving=Serving.PRIORITY)  # -> 2.0
    result = exposure(CODE, day, conforming + restated, DEFAULT_GATES)

    assert result.published is not None
    assert result.conforming_only is None
    assert result.shift is None
    assert not result.publishable_without_adjustment
    assert result.conforming_providers == 2


def test_shift_is_signed_against_the_counterfactual(make_obs, day):
    """Priority serving is restated *down* to standard, so the published
    value sits below one computed from conforming quotes alone. The sign can
    run either way here, where the compute benchmark's factors only ever mark
    cheap supply up -- which is the point of publishing it rather than
    asserting a direction."""
    conforming = _rows(make_obs, ("a", "b", "c", "d"), 3.0)
    restated = _rows(make_obs, ("e", "f"), 3.0, serving=Serving.PRIORITY)  # x0.5 -> 1.5
    result = exposure(CODE, day, conforming + restated, DEFAULT_GATES)

    assert result.conforming_only == pytest.approx(3.0)
    assert result.published is not None and result.published < 3.0
    assert result.shift is not None and result.shift < 0


def test_a_refused_good_reports_no_dependence(day):
    result = exposure("TIX-FRONTIER-OUT", day, [], DEFAULT_GATES)
    assert result.published is None
    assert result.total_quotes == 0
    assert result.conforming_share == 0.0
    assert result.by_factor == {}
