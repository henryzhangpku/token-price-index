"""Collect, restate, estimate, gate. One function, so there is one path."""

from __future__ import annotations

from datetime import date

from .estimator import estimate
from .models import Fixing, Observation
from .normalize import normalize_all
from .sources import all_observations, collected_at, snapshot_series
from .spec import CONTRACTS, DEFAULT_GATES, Gates


def default_index_date() -> date:
    """The date the observations pertain to, not the date the code ran.

    A fixing belongs to its inputs. Re-running Monday's observation set on
    Tuesday produces Monday's fixing, not a new one -- otherwise the same
    prices would generate a fresh value every day the pipeline is invoked,
    which is exactly the carry-forward behaviour the gates exist to prevent.
    """
    return collected_at().date()


def run(index_code: str, index_date: date | None = None,
        gates: Gates | None = None,
        observations: list[Observation] | None = None) -> Fixing:
    """Produce one fixing, published or withheld.

    ``observations`` defaults to the newest snapshot. It is a parameter so the
    series can re-run a past day against that day's file, rather than against
    today's -- the whole point of keeping the snapshots.
    """
    if observations is None:
        observations = all_observations()
    quotes, rejections = normalize_all(observations, index_code)
    return estimate(
        index_code,
        index_date or default_index_date(),
        quotes,
        rejections,
        gates or DEFAULT_GATES,
    )


def run_all(index_date: date | None = None,
            gates: Gates | None = None,
            observations: list[Observation] | None = None) -> dict[str, Fixing]:
    return {code: run(code, index_date, gates, observations) for code in CONTRACTS}


def run_series(gates: Gates | None = None) -> dict[str, list[Fixing]]:
    """Every contract's fixing on every collection date, oldest first.

    A withheld day stays in the list as a withheld Fixing rather than being
    dropped. A series that silently omits the days it could not price is a
    series with no gaps, which is a lie about the market -- and the gap is the
    thing the gates exist to produce.
    """
    series: dict[str, list[Fixing]] = {code: [] for code in CONTRACTS}
    for day, observations in snapshot_series():
        for code, fixing in run_all(day, gates, observations).items():
            series[code].append(fixing)
    return series
