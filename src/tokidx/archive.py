"""The durable record: immutable raw inputs plus an append-only tape.

The SQLite store is fast to query but is *derived state* -- it is rebuilt on
demand and never committed. Two things are durable instead:

``data/observations/YYYY-MM-DD.json``
    Every observation exactly as the seller published it, one file per
    collection date, never modified after it is written. This is what makes a
    published value reconstructible years later. ``sources`` owns these.

``data/tape.csv``
    The publication record: one row per revision, append-only. It cannot be
    derived from the snapshots, because it records *what was published and
    when* -- including values that were later superseded. Re-deriving it from
    inputs would quietly erase the revision history, which is the one thing a
    settlement dispute needs.

    Each row also names the snapshot it was computed from. That provenance
    link is what makes a value independently checkable: ``verify`` recomputes
    each live row from the file it names and refuses to pool by date.

Together they mean the database can be deleted at any time and rebuilt, while
nothing about the published history is lost. Same shape as the compute
benchmark's archive, deliberately.
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from .sources import SNAPSHOT_DIR

DATA_DIR = SNAPSHOT_DIR.parent
TAPE_PATH = DATA_DIR / "tape.csv"

TAPE_COLUMNS = [
    "index_code",
    "index_date",
    "revision",
    "status",
    "value",
    "provider_count",
    "observation_count",
    "dispersion",
    "withheld_reason",
    "methodology_version",
    "published_at",
    "superseded_at",
    "revision_reason",
    "snapshot",
]


def tape_path(path: Path | str | None = None) -> Path:
    return Path(path) if path is not None else TAPE_PATH


def append_to_tape(rows: list[dict], path: Path | str | None = None) -> Path:
    """Append publication records. Existing rows are never rewritten."""
    target = tape_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    exists = target.exists()
    with target.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TAPE_COLUMNS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in TAPE_COLUMNS})
    return target


def read_tape(path: Path | str | None = None) -> list[dict]:
    target = tape_path(path)
    if not target.exists():
        return []
    with target.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def stamp_superseded(path: Path | str | None = None) -> None:
    """Recompute ``superseded_at`` across the tape.

    A revision is superseded by the next revision of the same index and date.
    This is the one rewrite the tape permits, and it only ever fills a field
    that was previously blank -- values, dates and reasons are untouched.
    """
    rows = read_tape(path)
    if not rows:
        return
    latest: dict[tuple[str, str], int] = {}
    published: dict[tuple[str, str, int], str] = {}
    for row in rows:
        key = (row["index_code"], row["index_date"])
        revision = int(row["revision"])
        latest[key] = max(latest.get(key, -1), revision)
        published[(*key, revision)] = row["published_at"]
    for row in rows:
        key = (row["index_code"], row["index_date"])
        revision = int(row["revision"])
        if revision < latest[key]:
            row["superseded_at"] = published[(*key, revision + 1)]
    target = tape_path(path)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TAPE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def live_tape_values(path: Path | str | None = None) -> dict[tuple[str, str], dict]:
    """The current view: the highest revision for each index and date."""
    live: dict[tuple[str, str], dict] = {}
    for row in read_tape(path):
        key = (row["index_code"], row["index_date"])
        if key not in live or int(row["revision"]) > int(live[key]["revision"]):
            live[key] = row
    return live


def tape_row(stored: sqlite3.Row, snapshot: str) -> dict:
    """One tape row from the store's record of a fixing, plus its provenance.

    The store does not keep the snapshot name -- it keeps a run id, which is
    meaningless outside the database that issued it. The tape names the file,
    because the file is what survives.
    """
    return {
        "index_code": stored["index_code"],
        "index_date": stored["index_date"],
        "revision": stored["revision"],
        "status": stored["status"],
        "value": stored["value"],
        "provider_count": stored["provider_count"],
        "observation_count": stored["observation_count"],
        "dispersion": stored["dispersion"],
        "withheld_reason": stored["withheld_reason"],
        "methodology_version": stored["methodology_version"],
        "published_at": stored["published_at"],
        "superseded_at": stored["superseded_at"],
        "revision_reason": stored["revision_reason"],
        "snapshot": snapshot,
    }
