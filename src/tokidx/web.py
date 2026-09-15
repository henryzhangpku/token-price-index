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

from .normalize import normalize_all
from .pipeline import default_index_date, run_all, run_series
from .sensitivity import exposure
from .sources import all_observations, collected_at, collection_dates
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
    day = index_date or default_index_date()
    fixings = run_all(day)

    return {
        "index_date": day.isoformat(),
        "prices_read": collected_at().date().isoformat(),
        "collection_dates": collection_dates(),
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
        # One entry per collection date per contract, oldest first, computed
        # from that date's snapshot alone. A withheld day carries value null
        # rather than being dropped, so the chart can break the line there
        # instead of bridging a price that was never published.
        "series": {
            code: [
                {
                    "index_date": f.index_date.isoformat(),
                    "value": f.value if f.published else None,
                    "published": f.published,
                    "withheld_reason": f.withheld_reason,
                }
                for f in fixings_for_code
            ]
            for code, fixings_for_code in run_series().items()
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
                # How much of this number is the restatement schedule rather
                # than the market. Published so the weakest part of the
                # methodology is a figure a reader can argue with.
                "exposure": _exposure_entry(code, day),
            }
            for code, f in fixings.items()
        ],
    }


def _exposure_entry(code: str, day: date) -> dict[str, Any]:
    quotes, _ = normalize_all(all_observations(), code)
    e = exposure(code, day, quotes, DEFAULT_GATES)
    return {
        "published": e.published,
        "conforming_only": e.conforming_only,
        "shift": e.shift,
        "total_quotes": e.total_quotes,
        "conforming_quotes": e.conforming_quotes,
        "conforming_providers": e.conforming_providers,
        "weight_share_adjusted": round(e.weight_share_adjusted, 6),
        "by_factor": {k: round(v, 6) for k, v in e.by_factor.items()},
        "publishable_without_adjustment": e.publishable_without_adjustment,
    }


def write_bundle(out_dir: Path, index_date: date | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / BUNDLE
    path.write_text(
        json.dumps(build_bundle(index_date), indent=1) + "\n", encoding="utf-8"
    )
    return path
