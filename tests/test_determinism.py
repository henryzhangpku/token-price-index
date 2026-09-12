"""A fixing belongs to its inputs, not to the clock.

The first version dated each fixing with `date.today()`, so re-running an
unchanged observation set on a later day produced a fixing stamped with that
later day. CI caught it: the committed site bundle stopped matching a freshly
generated one the moment the date rolled over.

That was not a build annoyance. It is the carry-forward behaviour the gates
exist to prevent, arriving through the back door -- yesterday's prices quietly
becoming today's published value with no new observation behind it.
"""

from __future__ import annotations

from datetime import date

from tokidx.pipeline import default_index_date, run_all
from tokidx.sources import collected_at
from tokidx.web import build_bundle


def test_the_index_date_comes_from_the_observations() -> None:
    assert default_index_date() == collected_at().date()


def test_the_bundle_is_byte_identical_across_runs() -> None:
    """What CI actually checks, asserted here so it fails fast and locally."""
    assert build_bundle() == build_bundle()


def test_the_bundle_does_not_move_with_the_wall_clock() -> None:
    """Re-running an unchanged snapshot must not invent a new day."""
    assert build_bundle()["index_date"] == collected_at().date().isoformat()


def test_an_explicit_date_is_still_honoured() -> None:
    """Backfilling a past observation set stays possible; only the default moved."""
    day = date(2026, 1, 2)
    assert build_bundle(day)["index_date"] == "2026-01-02"
    assert all(f.index_date == day for f in run_all(day).values())


def test_values_do_not_depend_on_the_index_date() -> None:
    """The date labels a fixing. It must never change what the fixing says."""
    a = {c: f.value for c, f in run_all(date(2026, 1, 2)).items()}
    b = {c: f.value for c, f in run_all(date(2030, 12, 31)).items()}
    assert a == b
