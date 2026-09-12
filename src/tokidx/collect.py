"""Collection from a live source, and the one thing it cannot fix.

Until now every price in this repository was typed by hand on a single day.
This module reads them instead, from a public endpoint that publishes, per
model, the price each independent seller charges to serve the identical
weights.

That makes the series real. It does not make the sample independent, and the
distinction is the most important thing in this file.

**Every observation here arrives through one venue.** Twenty-seven sellers of
the same weights is twenty-seven sellers and *one* source. If that source
changed its schema, mis-parsed a field, or simply went away, the provider
count would not notice -- it would keep reporting twenty-seven right up until
the moment it reported none. The compute benchmark found exactly this shape in
its own inputs, where twelve providers turned out to be five venues with one
aggregator behind eight of them, and both count gates still passed when that
feed was removed.

So collected observations are recorded at the aggregated tier rather than the
list tier, carry the venue they came through, and the venue concentration is
published alongside the fixing rather than left for a reader to work out. A
single-venue index is not disqualified by that. It is disqualified by not
saying so.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime

from .models import Observation
from .spec import CONTRACTS, Direction, Serving, Tier

#: The venue every collected observation passes through. Named so that it can
#: be counted, and so a second venue can be added without renaming anything.
VENUE = "openrouter"

BASE = "https://openrouter.ai/api/v1"
USER_AGENT = "tokidx/0.1 (+https://github.com/henryzhangpku/token-price-index)"
TIMEOUT_SECONDS = 30

#: Prices arrive per token; the benchmark unit is per million.
PER_MILLION = 1_000_000


class CollectionError(RuntimeError):
    """A source failed. Recorded as a fact, never swallowed into an empty list."""


@dataclass
class VenueReport:
    """What one venue gave us, including when it gave us nothing."""

    venue: str
    ok: bool
    models_requested: int
    observations: int
    detail: str


def _get(path: str) -> dict:
    request = urllib.request.Request(f"{BASE}{path}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CollectionError(f"{path}: {exc}") from exc


def _price(pricing: dict, direction: Direction) -> float | None:
    key = "prompt" if direction is Direction.INPUT else "completion"
    raw = pricing.get(key)
    if raw is None:
        return None
    try:
        value = float(raw) * PER_MILLION
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def collect_model(model_id: str, direction: Direction, *, now: datetime) -> list[Observation]:
    """Every seller's price for one model, in one direction."""
    payload = _get(f"/models/{model_id}/endpoints")
    data = payload.get("data") or {}
    endpoints = data.get("endpoints") or []
    if not endpoints:
        raise CollectionError(f"{model_id}: source returned no sellers")

    observations: list[Observation] = []
    for endpoint in endpoints:
        seller = (endpoint.get("provider_name") or "").strip()
        if not seller:
            continue
        price = _price(endpoint.get("pricing") or {}, direction)
        if price is None:
            continue
        context = endpoint.get("context_length")
        observations.append(
            Observation(
                provider=seller,
                model=model_id,
                direction=direction,
                usd_per_mtok=price,
                serving=Serving.STANDARD,
                context_tokens=int(context) if context else 128_000,
                region="us",
                # Aggregated, not list. The seller sets the number, but we are
                # reading it through somebody else's page.
                tier=Tier.AGGREGATED,
                observed_at=now,
                source_url=f"{BASE}/models/{model_id}/endpoints",
                note=f"via {VENUE}",
            )
        )
    return observations


def collect(now: datetime | None = None) -> tuple[list[Observation], list[VenueReport]]:
    """Collect every contract's inputs. Returns observations and what happened.

    A venue that fails is reported rather than dropped. An empty list and a
    silent list look identical downstream, and only one of them should ever
    reach a gate.
    """
    moment = now or datetime.now(UTC)
    # Every contract, including the unindexable one. Collecting it matters:
    # a frontier good has to be refused for having one seller, not for having
    # an empty table, and those look identical on a page if nobody fetched it.
    wanted = {(c.model, c.direction) for c in CONTRACTS.values()}

    observations: list[Observation] = []
    failures: list[str] = []
    for model_id, direction in sorted(wanted):
        try:
            observations.extend(collect_model(model_id, direction, now=moment))
        except CollectionError as exc:
            failures.append(str(exc))

    report = VenueReport(
        venue=VENUE,
        ok=not failures and bool(observations),
        models_requested=len(wanted),
        observations=len(observations),
        detail="; ".join(failures) if failures else "all requested models returned sellers",
    )
    return observations, [report]


def venue_breakdown(observations: list[Observation]) -> dict[str, int]:
    """Sellers per venue. The number that matters more than the seller count."""
    by_venue: dict[str, set[str]] = {}
    for obs in observations:
        venue = obs.note.removeprefix("via ").strip() or "direct"
        by_venue.setdefault(venue, set()).add(obs.provider)
    return {venue: len(sellers) for venue, sellers in sorted(by_venue.items())}
