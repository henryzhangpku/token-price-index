"""What the index is measuring, and the constants a dispute would be about.

Everything here is a decision rather than a computation. It lives in one file
so that a counterparty can argue with a number instead of with a codebase, and
so that a reviewer can read the whole methodology on one screen.

The central decision is the first one below, and it is not obvious: an index
over *frontier* model pricing cannot be a market benchmark, because each
frontier model has exactly one seller. An index over *open-weight* model
pricing can, because the same weights are served by many providers competing
on price. See METHODOLOGY.md section 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum


class Tier(IntEnum):
    """How much a price proves, not how accurate it is.

    Note the difference from a compute-rental benchmark: a published token
    price *is* transactable. Anyone with a card can buy at it today. So the
    top tier here is genuinely populated, which is not true of GPU rental
    where published rate cards prove only that someone published a number.
    """

    #: Published API rate card, transactable today by any customer.
    LIST = 1
    #: Listed through an aggregator or router rather than the seller directly.
    AGGREGATED = 2
    #: Administrator's curated entry: sourced, dated, and hand-maintained.
    CURATED = 3


#: Weight by evidence class. A rate card you can transact against carries
#: full weight; the same number seen through a router carries less, because
#: routers add margin and their listing may not reflect the seller's terms.
TIER_WEIGHTS: dict[int, float] = {1: 1.00, 2: 0.60, 3: 0.25}


class Direction(StrEnum):
    """Input and output tokens are different goods and never mix.

    They are priced differently by every seller, consumed in different ratios
    by different workloads, and an index blending them would depend on an
    assumed ratio that is not observable. Two series, stated separately.
    """

    INPUT = "input"
    OUTPUT = "output"


class Serving(StrEnum):
    """How the request was served. The benchmark good is STANDARD."""

    #: Synchronous, uncached, standard priority.
    STANDARD = "standard"
    #: Asynchronous batch queue, typically discounted around 50%.
    BATCH = "batch"
    #: Cache read of previously submitted context, heavily discounted.
    CACHED = "cached"
    #: Priority or accelerated serving, typically at a premium.
    PRIORITY = "priority"


#: Multiplicative restatement onto STANDARD serving.
#:
#: Calibrated judgement, not observed spreads -- and unusually for this kind
#: of schedule, two of them are close to observable. Anthropic publishes batch
#: at 50% and cache reads at roughly a tenth of standard input, and OpenAI
#: publishes the same two ratios. Where two independent sellers publish the
#: same ratio it is a market convention rather than one vendor's discount
#: policy, which is the distinction that made the equivalent GPU spot factor
#: uncalibratable. See METHODOLOGY.md section 4.
SERVING_FACTORS: dict[Serving, float] = {
    Serving.STANDARD: 1.00,
    #: Batch is published at 50% off by every major seller, so restating a
    #: batch price to standard doubles it.
    Serving.BATCH: 2.00,
    #: Cache reads run at roughly 10% of standard input across sellers.
    Serving.CACHED: 10.00,
    #: Priority tiers are published at 2x by at least one seller.
    Serving.PRIORITY: 0.50,
}

#: An input needing more cumulative adjustment than this is describing the
#: adjustment schedule rather than the market, and is discarded.
#:
#: Set at 5.0 so that it binds on the cache factor. Restating a cache-read
#: price to standard multiplies it by ten, which means nine tenths of the
#: resulting number came from this file rather than from the seller -- that is
#: not an observation about standard pricing, it is the schedule talking. Batch
#: at 2x survives, because half the number is still the seller's.
#:
#: An earlier draft set this at 10.5, which no combination of factors could
#: reach: serving is a single enum and the context factor only reduces. A
#: ceiling nothing can touch is not a control, and a test caught it.
MAX_TOTAL_ADJUSTMENT = 5.0


@dataclass(frozen=True)
class Contract:
    """One good. Every input is restated onto this or discarded."""

    code: str
    display_name: str
    #: The model whose weights are being served. For an index to mean
    #: anything this must be identical across sellers, not merely similar.
    model: str
    direction: Direction
    #: Open weights served by many, or a proprietary model with one seller.
    open_weights: bool
    serving: Serving = Serving.STANDARD
    #: Context window the price applies to. Sellers tier long context.
    context_tokens: int = 128_000
    region: str = "us"
    unit: str = "USD per million tokens"

    @property
    def is_indexable(self) -> bool:
        """Whether a market benchmark over this good is even coherent.

        A proprietary model has one seller. An average of one seller's list
        price is that seller's list price wearing an index's clothes.
        """
        return self.open_weights


CONTRACTS: dict[str, Contract] = {
    # -- open weights: genuinely multi-seller, genuinely indexable ----------
    "TIX-K26-OUT": Contract(
        code="TIX-K26-OUT",
        display_name="Kimi K2.6 output",
        model="kimi-k2.6",
        direction=Direction.OUTPUT,
        open_weights=True,
    ),
    "TIX-K26-IN": Contract(
        code="TIX-K26-IN",
        display_name="Kimi K2.6 input",
        model="kimi-k2.6",
        direction=Direction.INPUT,
        open_weights=True,
    ),
    "TIX-GLM5-OUT": Contract(
        code="TIX-GLM5-OUT",
        display_name="GLM 5.x output",
        model="glm-5",
        direction=Direction.OUTPUT,
        open_weights=True,
    ),
    "TIX-MM27-OUT": Contract(
        code="TIX-MM27-OUT",
        display_name="MiniMax M2.7 output",
        model="minimax-m2.7",
        direction=Direction.OUTPUT,
        open_weights=True,
    ),
    # -- proprietary: included precisely to demonstrate the refusal ---------
    "TIX-FRONTIER-OUT": Contract(
        code="TIX-FRONTIER-OUT",
        display_name="Frontier proprietary output",
        model="frontier",
        direction=Direction.OUTPUT,
        open_weights=False,
    ),
}


@dataclass(frozen=True)
class Gates:
    """Every gate must hold or the value is withheld rather than published."""

    #: Lower than a compute benchmark's four, and that is a judgement worth
    #: arguing with: the open-model serving market is real but young, and a
    #: four-provider floor would withhold almost everything. Three is the
    #: minimum at which a median means anything at all.
    min_providers: int = 3
    min_observations: int = 3
    #: Robust coefficient of variation. Deliberately looser than a compute
    #: benchmark's 0.45: token prices for the same weights genuinely span a
    #: wide band because sellers differ on throughput, context and latency,
    #: none of which this contract pins down. Disclosed as a weakness.
    max_dispersion: float = 0.60
    #: No single provider may carry more than this share of total weight.
    max_provider_weight_share: float = 0.50
    #: A benchmark over a good with one seller is refused outright.
    require_indexable_good: bool = True


DEFAULT_GATES = Gates()

#: Decimal places a published value is stated to.
#:
#: Not cosmetic. The value is a weighted mean, and floating-point addition is
#: not associative, so two runs that sum the same contributions in a different
#: order can differ in the last bit. A number somebody settles against cannot
#: be "about" anything, so the fixing is rounded once, here, and the rounded
#: number is the published one. Four places is finer than any seller quotes.
#:
#: Found by a property test comparing two orderings of the same market.
PUBLICATION_DECIMALS = 4

#: Providers screened at this many robust sigma from the provider median.
OUTLIER_SIGMAS = 3.0

#: MAD -> standard deviation, 1 / inverse-normal(0.75). Not a choice.
MAD_TO_SIGMA = 1.4826

METHODOLOGY_VERSION = "0.1.0"
