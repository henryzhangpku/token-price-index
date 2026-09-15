"""Rebuild and verify the series from the archive.

Two operations, and the distinction between them is the point.

``rebuild``
    Reconstruct the working store from the tape and the snapshots. Values are
    not recomputed: they are read from the tape, which is the authoritative
    publication record, revision numbering intact. Recomputing them would
    renumber revisions and silently discard the history of what was published
    when. A date that has a snapshot and no tape row at all -- a day whose
    publish step never ran -- is backfilled, and the row says so: its
    ``published_at`` is the moment of the rebuild, not the day, and its reason
    names the file it came from. ``as-of`` then answers honestly that the value
    was not known until then.

``verify``
    Recompute every live value from the snapshot its tape row names and
    compare. This is the property that makes the benchmark auditable rather
    than merely logged: given the archive and the methodology version, anyone
    can derive the same numbers, or find out exactly where they cannot.

A verify failure is not necessarily a bug. It is the correct alarm when the
methodology changed without a version bump, when a snapshot was altered, or
when a value was published from inputs that were never archived. All three
are things a benchmark administrator has to be able to detect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .archive import append_to_tape, live_tape_values, read_tape, stamp_superseded, tape_row
from .pipeline import run_all
from .sources import read_snapshot, snapshot_paths
from .spec import CONTRACTS, DEFAULT_GATES, METHODOLOGY_VERSION, PUBLICATION_DECIMALS, Gates
from .store import Store

#: Published values are rounded once, to PUBLICATION_DECIMALS. Two runs over
#: the same inputs must agree to the last printed digit, so the tolerance is
#: half a unit in that place and no looser.
TOLERANCE = 0.5 * 10 ** (-PUBLICATION_DECIMALS)


@dataclass
class Mismatch:
    index_code: str
    index_date: str
    published: float | None
    recomputed: float | None
    detail: str


@dataclass
class VerifyReport:
    checked: int = 0
    matched: int = 0
    mismatches: list[Mismatch] = field(default_factory=list)
    unverifiable: list[Mismatch] = field(default_factory=list)
    methodology_drift: list[str] = field(default_factory=list)
    #: Tape rows naming a snapshot that is no longer on disk. Superseded
    #: revisions are not recomputed, so without this check their inputs could
    #: quietly disappear and nothing would notice.
    dangling: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches and not self.methodology_drift and not self.dangling


def rebuild(
    store: Store,
    tape: Path | None = None,
    snapshots: Path | None = None,
    gates: Gates | None = None,
) -> tuple[int, int]:
    """Restore the tape into the store, then backfill dates the tape never saw.

    Returns (rows restored, rows backfilled).
    """
    gates = gates or DEFAULT_GATES
    rows = read_tape(tape)
    for row in rows:
        store.restore(row)

    on_tape = {(r["index_code"], r["index_date"]) for r in rows}
    backfilled: list[dict] = []
    for path in snapshot_paths(snapshots):
        observations, moment = read_snapshot(path)
        day = moment.date()
        missing = [code for code in CONTRACTS if (code, day.isoformat()) not in on_tape]
        if not missing:
            continue
        fixings = run_all(day, gates, observations)
        run_id = store.start_run(
            moment, len(observations), {"snapshot": path.name, "backfilled": True}
        )
        reason = f"backfilled by rebuild from {path.name}; not published on the day"
        for code in missing:
            store.record(fixings[code], run_id=run_id, revision_reason=reason)
            backfilled.append(tape_row(store.latest(code, day), path.name))

    if backfilled:
        append_to_tape(backfilled, tape)
        stamp_superseded(tape)
    return len(rows), len(backfilled)


def verify(
    tape: Path | None = None, snapshots: Path | None = None, gates: Gates | None = None
) -> VerifyReport:
    """Recompute every live value from its named snapshot and compare to the tape."""
    gates = gates or DEFAULT_GATES
    report = VerifyReport()

    # A value is checked against the exact file that produced it, never
    # against a pooled day: a re-collection on one date is a different set of
    # inputs, and pooling would compare a published value against
    # observations that never existed together.
    available = {path.name: path for path in snapshot_paths(snapshots)}
    cache: dict[str, list] = {}

    # Integrity sweep across *every* revision, not just the live ones. A
    # superseded value is still a published value: someone may have settled
    # against it, and its inputs have to remain on file.
    for row in read_tape(tape):
        name = (row.get("snapshot") or "").strip()
        if name and name not in available:
            report.dangling.append(
                f"{row['index_code']} {row['index_date']} rev {row['revision']} "
                f"names missing snapshot {name}"
            )

    for (index_code, index_date), row in sorted(live_tape_values(tape).items()):
        report.checked += 1
        published = _as_float(row["value"])

        if row["methodology_version"] != METHODOLOGY_VERSION:
            report.methodology_drift.append(
                f"{index_code} {index_date} published under methodology "
                f"{row['methodology_version']}, current is {METHODOLOGY_VERSION}"
            )
            continue

        name = (row.get("snapshot") or "").strip()
        if not name:
            report.unverifiable.append(Mismatch(
                index_code, index_date, published, None,
                "tape row names no snapshot, so there are no inputs to recompute from",
            ))
            continue
        if name not in available:
            report.unverifiable.append(Mismatch(
                index_code, index_date, published, None,
                f"named snapshot {name} is missing from the archive",
            ))
            continue

        if name not in cache:
            cache[name] = read_snapshot(available[name])[0]
        fixing = run_all(date.fromisoformat(index_date), gates, cache[name])[index_code]
        recomputed = fixing.value if fixing.published else None

        if published is None and recomputed is None:
            report.matched += 1
        elif published is None or recomputed is None:
            report.mismatches.append(Mismatch(
                index_code, index_date, published, recomputed,
                "published and recomputed disagree on whether to publish at all",
            ))
        elif abs(published - recomputed) <= TOLERANCE:
            report.matched += 1
        else:
            report.mismatches.append(Mismatch(
                index_code, index_date, published, recomputed,
                f"differs by {abs(published - recomputed):.4f}",
            ))
    return report


def coverage(tape: Path | None = None, snapshots: Path | None = None) -> dict[str, int]:
    """How much archived history exists, for the operator's benefit."""
    rows = read_tape(tape)
    return {
        "snapshots": len(snapshot_paths(snapshots)),
        "tape_rows": len(rows),
        "index_dates": len({r["index_date"] for r in rows}),
        "indices": len(CONTRACTS),
    }


def _as_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    return float(value)
