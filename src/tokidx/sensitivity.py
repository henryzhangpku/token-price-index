"""How much of each fixing rests on judgement rather than on observation.

METHODOLOGY section 4 concedes that the serving and context factors are
calibrated judgement, bounds them, and caps their cumulative effect at 5x.
That is a defence of the containment, not of the numbers, and it leaves the
obvious question unanswered: *how much do they actually move the fixing?*

Without an answer, "the factors are bounded" is something a reader has to take
on trust. With one, the weakest part of the methodology becomes a published
figure that anyone can argue with.

Two measures, and the second is the one that matters:

*exposure*
    The share of contributing weight resting on sellers whose median includes
    at least one restated quote. A high number is not automatically bad -- it
    means sellers publish the good in more than one form, which they do.

*counterfactual shift*
    The fixing recomputed using only quotes that conformed to the contract as
    observed -- standard serving, benchmark context -- with every restated
    quote discarded. The gap between that and the published value is the part
    of the number that exists because of section 4 rather than because of the
    market.

The counterfactual is not a better estimate. Discarding restated quotes throws
away part of the sample and biases toward whichever sellers happen to publish
the benchmark configuration natively. It is a measurement of dependence, not a
proposal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .estimator import estimate
from .models import Quote
from .spec import CONTRACTS, Gates

#: Floating point noise: a quote whose factors multiply to within this of 1.0
#: was not meaningfully restated.
CONFORMING_TOLERANCE = 1e-9


@dataclass
class AdjustmentExposure:
    index_code: str
    published: float | None
    #: Recomputed from conforming quotes alone.
    conforming_only: float | None
    #: Relative gap between the two, or None when either is unavailable.
    shift: float | None
    total_quotes: int
    conforming_quotes: int
    conforming_providers: int
    #: Share of contributing weight carried by sellers whose median rests on
    #: at least one restated quote.
    weight_share_adjusted: float
    #: Share of quotes touched, per factor name.
    by_factor: dict[str, float] = field(default_factory=dict)
    #: Whether the index would still clear its gates on conforming quotes.
    publishable_without_adjustment: bool = False

    @property
    def conforming_share(self) -> float:
        return self.conforming_quotes / self.total_quotes if self.total_quotes else 0.0


def _is_conforming(quote: Quote) -> bool:
    return abs(quote.total_adjustment - 1.0) <= CONFORMING_TOLERANCE


def exposure(
    index_code: str, index_date: date, quotes: list[Quote], gates: Gates
) -> AdjustmentExposure:
    """Measure how much of one fixing depends on the restatement schedule."""
    published_fixing = estimate(index_code, index_date, quotes, [], gates)
    conforming = [q for q in quotes if _is_conforming(q)]
    conforming_fixing = estimate(index_code, index_date, conforming, [], gates)

    published = published_fixing.value if published_fixing.published else None
    counterfactual = conforming_fixing.value if conforming_fixing.published else None
    shift = None
    if published and counterfactual:
        shift = (published - counterfactual) / counterfactual

    # Attribute weight to sellers whose contribution rests on restated quotes.
    adjusted_providers = {q.provider for q in quotes if not _is_conforming(q)}
    contributing = published_fixing.contributing
    total_weight = sum(p.weight for p in contributing)
    adjusted_weight = sum(p.weight for p in contributing if p.provider in adjusted_providers)
    weight_share = adjusted_weight / total_weight if total_weight else 0.0

    by_factor: dict[str, int] = {}
    for quote in quotes:
        for adjustment in quote.adjustments:
            by_factor[adjustment.factor_name] = by_factor.get(adjustment.factor_name, 0) + 1

    return AdjustmentExposure(
        index_code=index_code,
        published=published,
        conforming_only=counterfactual,
        shift=shift,
        total_quotes=len(quotes),
        conforming_quotes=len(conforming),
        conforming_providers=len(conforming_fixing.contributing),
        weight_share_adjusted=weight_share,
        by_factor={
            name: count / len(quotes) for name, count in sorted(by_factor.items())
        }
        if quotes
        else {},
        publishable_without_adjustment=conforming_fixing.published,
    )


def exposure_all(
    index_date: date, quotes_by_code: dict[str, list[Quote]], gates: Gates
) -> list[AdjustmentExposure]:
    return [
        exposure(code, index_date, quotes_by_code.get(code, []), gates)
        for code in CONTRACTS
    ]
