"""Collect, restate, estimate, gate. One function, so there is one path."""

from __future__ import annotations

from datetime import date

from .estimator import estimate
from .models import Fixing
from .normalize import normalize_all
from .sources import COLLECTED_AT, all_observations
from .spec import CONTRACTS, DEFAULT_GATES, Gates


def default_index_date() -> date:
    """The date the observations pertain to, not the date the code ran.

    A fixing belongs to its inputs. Re-running Monday's observation set on
    Tuesday produces Monday's fixing, not a new one -- otherwise the same
    prices would generate a fresh value every day the pipeline is invoked,
    which is exactly the carry-forward behaviour the gates exist to prevent.
    """
    return COLLECTED_AT.date()


def run(index_code: str, index_date: date | None = None,
        gates: Gates | None = None) -> Fixing:
    """Produce one fixing, published or withheld."""
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
            gates: Gates | None = None) -> dict[str, Fixing]:
    return {code: run(code, index_date, gates) for code in CONTRACTS}
