"""Restate a published price as the benchmark good, or discard it and say why.

This is the step most open to argument, so it keeps its working: every quote
carries the factors applied to it and the raw number they were applied to.
"""

from __future__ import annotations

from .models import Adjustment, Observation, Quote, Rejection
from .spec import CONTRACTS, MAX_TOTAL_ADJUSTMENT, SERVING_FACTORS, Contract, Serving


def _context_factor(observed: int, benchmark: int) -> tuple[float, str] | None:
    """Long-context tiers are priced up; short ones are not priced down.

    Sellers publish a premium above a context threshold and a single rate
    below it, so this is a step rather than a curve. Deliberately flat: the
    data is far too thin to support a fitted shape and pretending otherwise
    would be inventing precision.
    """
    if observed <= benchmark:
        return None
    if observed <= 4 * benchmark:
        return 0.80, f"{observed:,}-token context restated to {benchmark:,}"
    return 0.65, f"{observed:,}-token context restated to {benchmark:,}"


def normalize(obs: Observation, contract: Contract) -> Quote | Rejection:
    """Express one observation as the contract, or explain the discard."""
    if obs.model != contract.model:
        return Rejection(obs.provider, "model_mismatch",
                         f"{obs.model} is not {contract.model}")
    if obs.direction != contract.direction:
        return Rejection(obs.provider, "direction_mismatch",
                         f"{obs.direction} tokens, contract is {contract.direction}")
    if obs.region != contract.region:
        return Rejection(obs.provider, "region_mismatch",
                         f"region {obs.region} outside {contract.region}")
    if obs.usd_per_mtok <= 0:
        return Rejection(obs.provider, "non_positive_price",
                         f"published {obs.usd_per_mtok}")

    adjustments: list[Adjustment] = []
    price = obs.usd_per_mtok

    if obs.serving is not contract.serving:
        factor = SERVING_FACTORS[obs.serving] / SERVING_FACTORS[contract.serving]
        adjustments.append(Adjustment(
            "serving", factor,
            f"{obs.serving.value} restated to {contract.serving.value}",
        ))
        price *= factor

    ctx = _context_factor(obs.context_tokens, contract.context_tokens)
    if ctx is not None:
        factor, reason = ctx
        adjustments.append(Adjustment("context", factor, reason))
        price *= factor

    total = price / obs.usd_per_mtok
    if total > MAX_TOTAL_ADJUSTMENT:
        return Rejection(
            obs.provider, "over_adjusted",
            f"{total:.2f}x cumulative exceeds the {MAX_TOTAL_ADJUSTMENT}x ceiling",
        )

    return Quote(
        index_code=contract.code,
        provider=obs.provider,
        raw_usd_per_mtok=obs.usd_per_mtok,
        usd_per_mtok=price,
        adjustments=adjustments,
        tier=obs.tier,
        source_url=obs.source_url,
        observed_at=obs.observed_at,
    )


def normalize_all(
    observations: list[Observation], index_code: str
) -> tuple[list[Quote], list[Rejection]]:
    contract = CONTRACTS[index_code]
    quotes: list[Quote] = []
    rejections: list[Rejection] = []
    for obs in observations:
        result = normalize(obs, contract)
        if isinstance(result, Quote):
            quotes.append(result)
        else:
            rejections.append(result)
    return quotes, rejections


def serving_check(observations: list[Observation]) -> list[tuple[str, str, float]]:
    """Where one seller publishes the same good two ways, the ratio is evidence.

    This is the only calibration available for the serving schedule, and it is
    the same argument that validates a commitment factor in a compute
    benchmark: a ratio between two prices from one seller is an observation of
    what that seller charges for the difference, not a guess.

    Returns (provider, model, observed standard/non-standard ratio) triples.
    """
    standard: dict[tuple[str, str, str], float] = {}
    other: dict[tuple[str, str, str, Serving], float] = {}
    for obs in observations:
        key = (obs.provider, obs.model, obs.direction.value)
        if obs.serving is Serving.STANDARD:
            standard[key] = obs.usd_per_mtok
        else:
            other[(*key, obs.serving)] = obs.usd_per_mtok

    out: list[tuple[str, str, float]] = []
    for (provider, model, direction, serving), price in sorted(
        other.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])
    ):
        base = standard.get((provider, model, direction))
        if base and price > 0:
            out.append((provider, f"{model} {direction} {serving.value}", base / price))
    return out
