"""The record types, and the boundary between what a seller said and what we did to it."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from .spec import Direction, Serving, Tier


@dataclass(frozen=True)
class Observation:
    """Exactly what a seller published, untouched.

    Kept verbatim so that any published value can be rebuilt from its inputs
    years later, and so that a dispute argues with the restatement rather than
    with an unrecoverable number.
    """

    #: Who sells it. Not who hosts it -- who takes the payment.
    provider: str
    #: The weights being served, normalised to a model key.
    model: str
    direction: Direction
    #: Price as published, in USD per million tokens.
    usd_per_mtok: float
    serving: Serving
    context_tokens: int
    region: str
    tier: Tier
    #: When the price was true, not when it was collected.
    observed_at: datetime
    #: Where it came from, so the number is checkable by hand.
    source_url: str
    note: str = ""


@dataclass(frozen=True)
class Adjustment:
    """One restatement step, kept so the arithmetic can be replayed."""

    factor_name: str
    factor: float
    reason: str


@dataclass
class Quote:
    """An observation restated as the benchmark good, or explained away."""

    index_code: str
    provider: str
    raw_usd_per_mtok: float
    usd_per_mtok: float
    adjustments: list[Adjustment]
    tier: Tier
    source_url: str
    observed_at: datetime

    @property
    def total_adjustment(self) -> float:
        return self.usd_per_mtok / self.raw_usd_per_mtok


@dataclass
class Rejection:
    """An observation that could not be restated, and why."""

    provider: str
    reason: str
    detail: str


@dataclass
class ProviderPoint:
    """One provider's single contribution. Listing ten models buys one vote."""

    provider: str
    price: float
    quote_count: int
    best_tier: Tier
    weight: float = 0.0
    screened_out: bool = False
    screen_reason: str | None = None


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str


@dataclass
class Flag:
    severity: str
    code: str
    detail: str


@dataclass
class Fixing:
    """One index, one day, one decision."""

    index_code: str
    index_date: date
    value: float | None
    dispersion: float | None
    providers: list[ProviderPoint] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    gates: list[GateResult] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    methodology_version: str = ""

    @property
    def contributing(self) -> list[ProviderPoint]:
        return [p for p in self.providers if not p.screened_out]

    @property
    def published(self) -> bool:
        return all(g.passed for g in self.gates) and self.value is not None

    @property
    def withheld_reason(self) -> str | None:
        failed = [g for g in self.gates if not g.passed]
        if not failed:
            return None
        return "; ".join(f"{g.name}: {g.detail}" for g in failed)
