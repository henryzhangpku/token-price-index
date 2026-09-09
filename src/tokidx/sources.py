"""The observation set: what sellers published, when, and where it was read.

Every entry carries a source URL and an observation date, so any published
value can be checked by hand against the seller's own page. Prices move often
-- the providers themselves warn as much -- so a stale entry is a defect and
the collection date is part of the record rather than metadata.

This is a curated snapshot rather than a live scrape, and that is a real
limitation, disclosed in METHODOLOGY.md section 6. The pipeline is written so
that a live collector drops in behind the same interface; what would change is
the freshness of the inputs, not the estimator, the gates or the archive.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .models import Observation
from .spec import Direction, Serving, Tier

#: The date this price set was read from the sellers' published pages.
COLLECTED_AT = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _obs(
    provider: str,
    model: str,
    inp: float,
    out: float,
    url: str,
    tier: Tier = Tier.LIST,
    serving: Serving = Serving.STANDARD,
    context: int = 128_000,
    note: str = "",
) -> list[Observation]:
    """One published price pair becomes two observations, never one blended."""
    return [
        Observation(
            provider=provider,
            model=model,
            direction=direction,
            usd_per_mtok=price,
            serving=serving,
            context_tokens=context,
            region="us",
            tier=tier,
            observed_at=COLLECTED_AT,
            source_url=url,
            note=note,
        )
        for direction, price in ((Direction.INPUT, inp), (Direction.OUTPUT, out))
    ]


#: Open-weight models served by several independent sellers. This is where
#: price discovery actually happens, and the only place an index is coherent.
OPEN_WEIGHT: list[Observation] = [
    # -- Kimi K2.6: the cleanest example. Same weights, three sellers. ------
    *_obs("deepinfra", "kimi-k2.6", 0.75, 3.50, "https://deepinfra.com/pricing"),
    *_obs("fireworks", "kimi-k2.6", 0.95, 4.00, "https://fireworks.ai/pricing"),
    *_obs("together", "kimi-k2.6", 1.20, 4.50, "https://www.together.ai/pricing"),
    # -- GLM 5.x -----------------------------------------------------------
    *_obs("fireworks", "glm-5", 1.40, 4.40, "https://fireworks.ai/pricing"),
    *_obs("together", "glm-5", 1.40, 4.40, "https://www.together.ai/pricing"),
    # -- MiniMax M2.7 ------------------------------------------------------
    *_obs("fireworks", "minimax-m2.7", 0.30, 1.20, "https://fireworks.ai/pricing"),
    *_obs("together", "minimax-m2.7", 0.30, 1.20, "https://www.together.ai/pricing"),
]

#: Proprietary frontier models. Included so the index can demonstrate what it
#: does with them, which is refuse. Each of these has exactly one seller.
FRONTIER: list[Observation] = [
    *_obs("anthropic", "frontier", 10.00, 50.00, "https://claude.com/pricing",
          note="Fable 5.1"),
    *_obs("anthropic", "frontier", 5.00, 25.00, "https://claude.com/pricing",
          note="Opus 5"),
    *_obs("openai", "frontier", 10.00, 50.00, "https://openai.com/api/pricing/",
          note="GPT-6 Astra"),
    *_obs("openai", "frontier", 4.00, 20.00, "https://openai.com/api/pricing/",
          note="GPT-5.6 Sol; seller describes the rate as promotional"),
]

#: Non-standard serving, kept to exercise the restatement path. A batch price
#: restated to standard should land on the seller's own standard price, which
#: is the closest thing to a calibration check this schedule has.
NON_STANDARD: list[Observation] = [
    *_obs("deepinfra", "kimi-k2.6", 0.375, 1.75, "https://deepinfra.com/pricing",
          serving=Serving.BATCH, note="batch queue, published at 50% off"),
]


def all_observations() -> list[Observation]:
    return [*OPEN_WEIGHT, *FRONTIER, *NON_STANDARD]
