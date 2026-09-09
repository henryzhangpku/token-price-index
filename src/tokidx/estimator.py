"""Turn restated quotes into one number, or refuse to.

The estimator is built around a question rather than around elegance: what
would a seller who wanted the print somewhere else actually do? Each defence
below answers one specific move.
"""

from __future__ import annotations

import statistics
from datetime import date

from .models import Fixing, Flag, GateResult, ProviderPoint, Quote, Rejection
from .spec import (
    CONTRACTS,
    MAD_TO_SIGMA,
    METHODOLOGY_VERSION,
    OUTLIER_SIGMAS,
    TIER_WEIGHTS,
    Gates,
)

#: With no spread, a sigma test is undefined. Fall back to a symmetric ratio
#: band against the consensus. A percentage test would be wrong twice: it
#: flags a seller 50% below consensus, which is ordinary here, and it caps at
#: 100% on the downside, so it could never catch a near-zero price against a
#: three-dollar market.
DEGENERATE_RATIO = 3.0
RATIO_EPSILON = 1e-9


def collapse_to_providers(quotes: list[Quote]) -> list[ProviderPoint]:
    """One seller, one vote, whatever its catalogue size.

    A provider listing the same weights under six SKUs, or serving forty
    models, gets a single median. Flooding the venue buys nothing.
    """
    by_provider: dict[str, list[Quote]] = {}
    for q in quotes:
        by_provider.setdefault(q.provider, []).append(q)

    points: list[ProviderPoint] = []
    for provider, group in sorted(by_provider.items()):
        prices = sorted(q.usd_per_mtok for q in group)
        points.append(ProviderPoint(
            provider=provider,
            price=statistics.median(prices),
            quote_count=len(group),
            best_tier=min(q.tier for q in group),
        ))
    return points


def screen(points: list[ProviderPoint]) -> list[Flag]:
    """Remove providers far from where the market agrees it is.

    Median absolute deviation rather than standard deviation, because MAD has
    a 50% breakdown point and standard deviation has none -- the outlier being
    screened for inflates the very yardstick measuring it.
    """
    live = [p for p in points if not p.screened_out]
    if len(live) < 3:
        return []

    prices = [p.price for p in live]
    median = statistics.median(prices)
    mad = statistics.median([abs(x - median) for x in prices])
    sigma = mad * MAD_TO_SIGMA
    flags: list[Flag] = []

    if sigma <= 0:
        # Every honest seller agrees exactly, so there is no scale to measure
        # distance in. This is the configuration where one absurd quote does
        # the most damage and the sigma test does the least.
        if median <= 0:
            return flags
        for p in live:
            if p.price <= 0:
                continue
            ratio = max(p.price / median, median / p.price)
            if ratio > DEGENERATE_RATIO * (1 + RATIO_EPSILON):
                p.screened_out = True
                p.screen_reason = f"{ratio:.1f}x from an exact consensus"
                flags.append(Flag("warn", "outlier_screened_degenerate",
                                  f"{p.provider} at {p.price:.3f}: {ratio:.1f}x "
                                  f"from a consensus of {median:.3f}, MAD zero"))
        return flags

    for p in live:
        deviation = abs(p.price - median) / sigma
        if deviation > OUTLIER_SIGMAS:
            p.screened_out = True
            p.screen_reason = f"{deviation:.1f} robust sigma from median"
            flags.append(Flag("info", "outlier_screened",
                              f"{p.provider} at {p.price:.3f} screened "
                              f"({deviation:.1f} sigma from {median:.3f})"))
    return flags


def assign_weights(points: list[ProviderPoint], gates: Gates) -> list[Flag]:
    """Weight by evidence class, then cap any one seller's influence."""
    live = [p for p in points if not p.screened_out]
    if not live:
        return []

    for p in live:
        p.weight = TIER_WEIGHTS[int(p.best_tier)]

    flags: list[Flag] = []
    # Below 1/cap providers there is no allocation satisfying the cap at all.
    # Such an index fails the provider gate anyway; leave the weights alone
    # and let the gate do the refusing rather than driving every weight to nil.
    if len(live) * gates.max_provider_weight_share < 1.0:
        return flags

    for _ in range(len(live)):
        total = sum(p.weight for p in live)
        if total <= 0:
            break
        over = [p for p in live if p.weight / total > gates.max_provider_weight_share]
        if not over:
            break
        for p in over:
            others = total - p.weight
            capped = gates.max_provider_weight_share * others / (
                1 - gates.max_provider_weight_share
            )
            if capped < p.weight:
                flags.append(Flag("warn", "provider_weight_capped",
                                  f"{p.provider} {p.weight:.2f} -> {capped:.2f} "
                                  f"to respect the {gates.max_provider_weight_share:.0%} cap"))
                p.weight = capped
    return flags


def dispersion(points: list[ProviderPoint]) -> float | None:
    """Robust coefficient of variation, or undefined below three providers.

    Undefined is not a pass. A fixing whose disagreement cannot be measured
    is refused rather than assumed to be tight.
    """
    prices = [p.price for p in points]
    if len(prices) < 3:
        return None
    median = statistics.median(prices)
    if median <= 0:
        return None
    mad = statistics.median([abs(x - median) for x in prices])
    return (mad * MAD_TO_SIGMA) / median


def estimate(
    index_code: str,
    index_date: date,
    quotes: list[Quote],
    rejections: list[Rejection],
    gates: Gates,
) -> Fixing:
    contract = CONTRACTS[index_code]
    points = collapse_to_providers(quotes)
    flags = screen(points)
    flags += assign_weights(points, gates)

    contributing = [p for p in points if not p.screened_out]
    disp = dispersion(contributing)
    total_weight = sum(p.weight for p in contributing)
    value = (
        sum(p.price * p.weight for p in contributing) / total_weight
        if total_weight > 0
        else None
    )
    observations = sum(p.quote_count for p in contributing)

    results = [
        GateResult(
            "indexable_good",
            not gates.require_indexable_good or contract.is_indexable,
            "open weights, multiple sellers"
            if contract.is_indexable
            else "proprietary model: one seller per model, so cross-seller "
                 "price discovery does not exist",
        ),
        GateResult(
            "min_providers",
            len(contributing) >= gates.min_providers,
            f"{len(contributing)} of {gates.min_providers} required",
        ),
        GateResult(
            "min_observations",
            observations >= gates.min_observations,
            f"{observations} of {gates.min_observations} required",
        ),
        GateResult(
            "dispersion",
            disp is not None and disp <= gates.max_dispersion,
            f"{disp:.3f} against ceiling {gates.max_dispersion}"
            if disp is not None
            else "not computable below three providers",
        ),
    ]

    return Fixing(
        index_code=index_code,
        index_date=index_date,
        value=value if all(g.passed for g in results) else None,
        dispersion=disp,
        providers=points,
        rejections=rejections,
        gates=results,
        flags=flags,
        methodology_version=METHODOLOGY_VERSION,
    )
