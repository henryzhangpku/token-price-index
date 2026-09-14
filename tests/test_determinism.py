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
    """CI used to catch this by diffing a committed bundle; it is asserted here
    instead, because the bundle is no longer committed."""
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


def test_each_date_in_the_series_is_priced_from_its_own_snapshot() -> None:
    """The point of keeping snapshots: a past fixing follows from past inputs.

    run() defaults to the newest snapshot, so a series built without passing
    observations would re-price every historical date with today's prices --
    a flat line that looks like a stable market and is actually one day
    repeated.
    """
    from tokidx.pipeline import run_series
    from tokidx.sources import snapshot_series

    days = [day for day, _ in snapshot_series()]
    for fixings in run_series().values():
        assert [f.index_date for f in fixings] == days

    if len(days) > 1:
        # At least one index must actually move, or the per-date wiring is
        # silently returning the same snapshot for every date.
        moved = any(
            len({f.value for f in fixings if f.published}) > 1
            for fixings in run_series().values()
        )
        assert moved, "no index moved across dates; the series is one day repeated"


def test_a_withheld_date_stays_in_the_series_as_a_gap() -> None:
    """Dropping withheld days would produce a series with no gaps, which is a
    lie about a market the gates refused to price."""
    from tokidx.web import build_bundle

    bundle = build_bundle()
    for code, entries in bundle["series"].items():
        assert len(entries) == len(bundle["collection_dates"]), code
        for e in entries:
            assert (e["value"] is None) is (not e["published"]), (code, e)
