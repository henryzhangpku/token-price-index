"""Properties the estimator must hold for any market, not just plausible ones.

The hand-written tests choose their inputs, and everything a human chooses
looks like a market: prices a few percent apart, no exact ties, nothing
absurd. That is precisely the blind spot that let the unreachable adjustment
ceiling through -- every fixture was realistic, and no realistic fixture ever
stacked enough factors to test the bound.

These generate the inputs instead, and deliberately generate the shapes nobody
would write down: every seller quoting the identical price, prices spanning
five orders of magnitude, two-seller markets, and an adversary quoting
whatever it likes.

The price grid is coarse on purpose. Sampling continuous floats makes exact
ties vanishingly unlikely, and the degenerate branch of the screen -- where
MAD collapses to zero and a sigma test is undefined -- only exists because
exact ties happen.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from tokidx.estimator import collapse_to_providers, dispersion, estimate, screen
from tokidx.models import Adjustment, Quote
from tokidx.spec import DEFAULT_GATES, PUBLICATION_DECIMALS, Gates, Tier

DAY = date(2026, 9, 8)
MOMENT = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)

#: A coarse grid, so exact ties across every seller occur often enough to
#: exercise the degenerate branch of the screen.
prices = st.integers(min_value=5, max_value=4000).map(lambda cents: cents / 100.0)

#: Catalogue size. A seller listing forty SKUs must not out-vote one listing a
#: single SKU, so the generator has to be able to produce both.
catalogue = st.integers(min_value=1, max_value=6)

PROPERTY_SETTINGS = settings(
    max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)


def quote(provider: str, price: float, tier: Tier = Tier.LIST) -> Quote:
    return Quote(
        index_code="TIX-K26-OUT",
        provider=provider,
        raw_usd_per_mtok=price,
        usd_per_mtok=price,
        adjustments=[Adjustment("none", 1.0, "already the benchmark good")],
        tier=tier,
        source_url="https://example.invalid/pricing",
        observed_at=MOMENT,
    )


def market(price_list: list[float], per_seller: int = 1) -> list[Quote]:
    quotes: list[Quote] = []
    for i, price in enumerate(price_list):
        for _ in range(per_seller):
            quotes.append(quote(f"seller{i:02d}", price))
    return quotes


# ---------------------------------------------------------------------------
# The value
# ---------------------------------------------------------------------------


@given(st.lists(prices, min_size=3, max_size=9))
@PROPERTY_SETTINGS
def test_the_value_never_escapes_the_contributing_prices(price_list):
    """A weighted average of numbers cannot sit outside them."""
    fixing = estimate("TIX-K26-OUT", DAY, market(price_list), [], DEFAULT_GATES)
    assume(fixing.published)
    contributing = [p.price for p in fixing.contributing]
    assert min(contributing) - 1e-9 <= fixing.value <= max(contributing) + 1e-9


@given(st.lists(prices, min_size=3, max_size=7), catalogue)
@PROPERTY_SETTINGS
def test_one_seller_one_vote_whatever_the_catalogue_size(price_list, per_seller):
    """Listing the same weights six times must buy no extra influence."""
    one = estimate("TIX-K26-OUT", DAY, market(price_list, 1), [], DEFAULT_GATES)
    many = estimate("TIX-K26-OUT", DAY, market(price_list, per_seller), [], DEFAULT_GATES)
    assume(one.value is not None and many.value is not None)
    assert one.value == many.value


@given(st.lists(prices, min_size=3, max_size=7))
@PROPERTY_SETTINGS
def test_the_order_observations_arrive_in_changes_nothing(price_list):
    """A fixing that depends on file order is not reproducible."""
    forward = estimate("TIX-K26-OUT", DAY, market(price_list), [], DEFAULT_GATES)
    backward = estimate("TIX-K26-OUT", DAY, market(list(reversed(price_list))), [],
                        DEFAULT_GATES)
    assume(forward.value is not None and backward.value is not None)
    assert forward.value == backward.value


@given(st.lists(prices, min_size=3, max_size=7),
       st.sampled_from([0.5, 2.0, 10.0, 100.0]))
@PROPERTY_SETTINGS
def test_the_value_scales_with_the_market(price_list, factor):
    """Re-denominating every price re-denominates the fixing, and nothing else.

    Exact equality is the wrong claim: the value is rounded to the published
    precision, so scaling and rounding do not commute. The honest property is
    that they agree to within one published unit on each side.
    """
    base = estimate("TIX-K26-OUT", DAY, market(price_list), [], DEFAULT_GATES)
    scaled = estimate("TIX-K26-OUT", DAY, market([p * factor for p in price_list]), [],
                      DEFAULT_GATES)
    assume(base.value is not None and scaled.value is not None)
    unit = 10.0 ** -PUBLICATION_DECIMALS
    assert abs(scaled.value - base.value * factor) <= unit * (1.0 + factor)


def pytest_approx(value: float, rel: float = 1e-9):
    import pytest

    return pytest.approx(value, rel=rel)


# ---------------------------------------------------------------------------
# The screen
# ---------------------------------------------------------------------------


@given(
    st.integers(min_value=3, max_value=8),
    prices,
    st.sampled_from([50.0, 500.0, 5000.0]),
)
@PROPERTY_SETTINGS
def test_an_absurdly_high_quote_is_always_screened(honest, consensus, multiple):
    """The case that breaks a naive screen: unanimity plus one adversary.

    When every honest seller quotes exactly the same price, MAD collapses to
    zero and a sigma test is undefined. An implementation that returns without
    screening at that point publishes whatever the adversary asked for.
    """
    quotes = market([consensus] * honest) + [quote("adversary", consensus * multiple)]
    points = collapse_to_providers(quotes)
    screen(points)
    adversary = next(p for p in points if p.provider == "adversary")
    assert adversary.screened_out, f"survived at {multiple:.0f}x consensus"


@given(st.integers(min_value=3, max_value=8), prices)
@PROPERTY_SETTINGS
def test_a_near_zero_quote_is_screened_from_a_unanimous_market(honest, consensus):
    """The tail a percentage test can never defend.

    A percentage band caps at 100% on the downside, so it could never catch a
    seller quoting a cent against a three-dollar market. The ratio fallback is
    symmetric in ratio, which is the right shape for a price.
    """
    assume(consensus > 0.5)
    quotes = market([consensus] * honest) + [quote("adversary", consensus / 100.0)]
    points = collapse_to_providers(quotes)
    screen(points)
    adversary = next(p for p in points if p.provider == "adversary")
    assert adversary.screened_out, f"survived at 1/100 of {consensus}"


@given(st.lists(prices, min_size=3, max_size=8))
@PROPERTY_SETTINGS
def test_screening_never_removes_every_seller(price_list):
    """A screen that empties the market has measured nothing."""
    points = collapse_to_providers(market(price_list))
    screen(points)
    assert any(not p.screened_out for p in points)


# ---------------------------------------------------------------------------
# Dispersion and weights
# ---------------------------------------------------------------------------


@given(st.lists(prices, min_size=3, max_size=8),
       st.sampled_from([0.5, 2.0, 10.0, 100.0]))
@PROPERTY_SETTINGS
def test_dispersion_is_scale_free(price_list, factor):
    """One ceiling has to fit an index priced in cents and one priced in tens."""
    base = collapse_to_providers(market(price_list))
    scaled = collapse_to_providers(market([p * factor for p in price_list]))
    a, b = dispersion(base), dispersion(scaled)
    assume(a is not None and b is not None)
    assert b == pytest_approx(a, rel=1e-6)


@given(st.lists(prices, min_size=3, max_size=9))
@PROPERTY_SETTINGS
def test_no_seller_exceeds_the_weight_cap_when_the_cap_is_satisfiable(price_list):
    """With enough sellers the cap must bind; with too few it must not try.

    Three sellers under a 50% cap is satisfiable. Two is not -- no allocation
    of two weights summing to one keeps both at or below a half without
    driving something to zero -- and in that case the provider-count gate is
    what refuses, not the cap.
    """
    points = collapse_to_providers(market(price_list))
    screen(points)
    assign = __import__("tokidx.estimator", fromlist=["assign_weights"]).assign_weights
    assign(points, DEFAULT_GATES)
    contributing = [p for p in points if not p.screened_out]
    total = sum(p.weight for p in contributing)
    assume(total > 0)
    cap = DEFAULT_GATES.max_provider_weight_share
    if len(contributing) * cap >= 1.0 - 1e-9:
        for point in contributing:
            assert point.weight / total <= cap + 1e-9


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


@given(st.lists(prices, min_size=1, max_size=2))
@PROPERTY_SETTINGS
def test_a_market_thinner_than_the_floor_is_never_published(price_list):
    """Withholding is the outcome for a thin market, not a smaller sample."""
    fixing = estimate("TIX-K26-OUT", DAY, market(price_list), [], DEFAULT_GATES)
    assert not fixing.published
    assert fixing.withheld_reason


@given(st.lists(prices, min_size=3, max_size=8))
@PROPERTY_SETTINGS
def test_a_published_fixing_always_carries_a_value_and_a_withheld_one_never_does(price_list):
    """The two states have to be distinguishable without reading the gates."""
    fixing = estimate("TIX-K26-OUT", DAY, market(price_list), [], DEFAULT_GATES)
    if fixing.published:
        assert fixing.value is not None
        assert fixing.withheld_reason is None
    else:
        assert fixing.withheld_reason


@given(st.lists(prices, min_size=3, max_size=8))
@PROPERTY_SETTINGS
def test_a_stricter_gate_never_publishes_more(price_list):
    """Tightening a threshold must narrow what prints, never widen it."""
    loose = estimate("TIX-K26-OUT", DAY, market(price_list), [], DEFAULT_GATES)
    strict = estimate(
        "TIX-K26-OUT", DAY, market(price_list), [],
        Gates(min_providers=4, min_observations=8, max_dispersion=0.45,
              max_provider_weight_share=0.35),
    )
    if strict.published:
        assert loose.published
