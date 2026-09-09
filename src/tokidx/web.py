"""Dump the fixings as JSON for the static site.

The site is a reader, so the risk it carries is not a wrong number but a
*disagreeing* one: a page recomputing something subtly different from what the
CLI prints would misrepresent the record while looking authoritative. So it
takes the same pipeline output, serialised, rather than reimplementing
anything in JavaScript.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from .pipeline import run_all
from .sources import COLLECTED_AT, all_observations
from .spec import (
    CONTRACTS,
    DEFAULT_GATES,
    MAX_TOTAL_ADJUSTMENT,
    METHODOLOGY_VERSION,
    OUTLIER_SIGMAS,
    SERVING_FACTORS,
    TIER_WEIGHTS,
)

BUNDLE = "fixings.json"


def build_bundle(index_date: date | None = None) -> dict[str, Any]:
    day = index_date or date.today()
    fixings = run_all(day)

    return {
        "generated_for": day.isoformat(),
        "prices_read": COLLECTED_AT.date().isoformat(),
        "methodology_version": METHODOLOGY_VERSION,
        "observation_count": len(all_observations()),
        "disclaimer": (
            "A demonstration of which parts of the inference market can carry a "
            "reference price. Do not settle anything against these values."
        ),
        "spec": {
            "tier_weights": {str(k): v for k, v in TIER_WEIGHTS.items()},
            "serving_factors": {k.value: v for k, v in SERVING_FACTORS.items()},
            "max_total_adjustment": MAX_TOTAL_ADJUSTMENT,
            "outlier_sigmas": OUTLIER_SIGMAS,
            "gates": {
                "indexable_good": DEFAULT_GATES.require_indexable_good,
                "min_providers": DEFAULT_GATES.min_providers,
                "min_observations": DEFAULT_GATES.min_observations,
                "max_dispersion": DEFAULT_GATES.max_dispersion,
                "max_provider_weight_share": DEFAULT_GATES.max_provider_weight_share,
            },
        },
        "indices": [
            {
                "code": code,
                "display_name": CONTRACTS[code].display_name,
                "model": CONTRACTS[code].model,
                "direction": CONTRACTS[code].direction.value,
                "indexable": CONTRACTS[code].is_indexable,
                "value": f.value,
                "dispersion": f.dispersion,
                "published": f.published,
                "withheld_reason": f.withheld_reason,
                "gates": [
                    {"name": g.name, "passed": g.passed, "detail": g.detail}
                    for g in f.gates
                ],
                "providers": [
                    {
                        "provider": p.provider,
                        "price": round(p.price, 6),
                        "quotes": p.quote_count,
                        "tier": int(p.best_tier),
                        "weight": round(p.weight, 6),
                        "screened_out": p.screened_out,
                        "screen_reason": p.screen_reason,
                    }
                    for p in sorted(f.providers, key=lambda p: p.price)
                ],
                "rejections": len(f.rejections),
            }
            for code, f in fixings.items()
        ],
    }


def write_bundle(out_dir: Path, index_date: date | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / BUNDLE
    path.write_text(
        json.dumps(build_bundle(index_date), indent=1) + "\n", encoding="utf-8"
    )
    return path
