"""Where observations come from: a dated snapshot on disk.

Collection and estimation are deliberately separate processes. ``tokidx
collect`` reaches the network once and writes exactly what it saw to a dated
file; everything downstream reads that file and never reaches anywhere.

That split is what makes a published value reproducible. A pipeline that
fetches and computes in one pass can only be re-run against a market that has
since moved, so its own history is unverifiable -- you can never show that
Tuesday's number follows from Tuesday's inputs, because Tuesday's inputs are
gone. Snapshots are committed for the same reason.

The snapshots are also the series. One file per collection date, and the tape
grows by one file a day rather than by a value appearing from nowhere.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from .models import Observation
from .spec import Direction, Serving, Tier

SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "data" / "observations"


class NoSnapshots(FileNotFoundError):
    """Raised when nothing has been collected yet.

    Deliberately loud. An empty observation list and an uncollected market look
    identical to every gate downstream, and only one of them is a market fact.
    """


class SnapshotExists(FileExistsError):
    """Raised rather than overwriting a snapshot that is already on disk.

    A snapshot is immutable once written: tape rows name it, and ``verify``
    recomputes published values from it. A second collection on the same date
    would silently replace the inputs behind a value already on the tape,
    which is the one thing the archive exists to prevent.
    """


def snapshot_paths(directory: Path | None = None) -> list[Path]:
    """Every collection snapshot, oldest first."""
    root = directory or SNAPSHOT_DIR
    if not root.exists():
        return []
    return sorted(root.glob("*.json"))


def latest_snapshot(directory: Path | None = None) -> Path:
    paths = snapshot_paths(directory)
    if not paths:
        raise NoSnapshots(
            f"no snapshots in {directory or SNAPSHOT_DIR}; run `tokidx collect` first"
        )
    return paths[-1]


def write_snapshot(
    observations: list[Observation],
    collected_at: datetime,
    *,
    venues: list[dict] | None = None,
    directory: Path | None = None,
) -> Path:
    """Write one collection to a dated file, verbatim."""
    root = directory or SNAPSHOT_DIR
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{collected_at.date().isoformat()}.json"
    if path.exists():
        raise SnapshotExists(
            f"{path.name} is already on disk; snapshots are immutable and a tape "
            "row may name this one"
        )
    payload = {
        "collected_at": collected_at.isoformat(),
        "venues": venues or [],
        "observations": [
            {
                "provider": o.provider,
                "model": o.model,
                "direction": o.direction.value,
                "usd_per_mtok": o.usd_per_mtok,
                "serving": o.serving.value,
                "context_tokens": o.context_tokens,
                "region": o.region,
                "tier": int(o.tier),
                "observed_at": o.observed_at.isoformat(),
                "source_url": o.source_url,
                "note": o.note,
            }
            for o in observations
        ],
    }
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_snapshot(path: Path) -> tuple[list[Observation], datetime]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    collected_at = datetime.fromisoformat(payload["collected_at"])
    if collected_at.tzinfo is None:
        collected_at = collected_at.replace(tzinfo=UTC)
    observations = [
        Observation(
            provider=row["provider"],
            model=row["model"],
            direction=Direction(row["direction"]),
            usd_per_mtok=float(row["usd_per_mtok"]),
            serving=Serving(row["serving"]),
            context_tokens=int(row["context_tokens"]),
            region=row["region"],
            tier=Tier(int(row["tier"])),
            observed_at=datetime.fromisoformat(row["observed_at"]),
            source_url=row["source_url"],
            note=row.get("note", ""),
        )
        for row in payload["observations"]
    ]
    return observations, collected_at


def all_observations(directory: Path | None = None) -> list[Observation]:
    observations, _ = read_snapshot(latest_snapshot(directory))
    return observations


def collected_at(directory: Path | None = None) -> datetime:
    """When the observations behind the current fixing were read."""
    _, moment = read_snapshot(latest_snapshot(directory))
    return moment


def collection_dates(directory: Path | None = None) -> list[str]:
    return [p.stem for p in snapshot_paths(directory)]


def snapshot_series(
    directory: Path | None = None,
) -> list[tuple[date, list[Observation]]]:
    """Every collection date and what was read that day, oldest first.

    ``all_observations`` reads only the newest file on purpose: a fixing must
    never mix two days' prices. The series is that same rule applied once per
    file rather than once, so each date's value follows from that date's inputs
    and from nothing else -- which is the only construction that lets a past
    value be checked after the fact.
    """
    return [(moment.date(), observations)
            for path in snapshot_paths(directory)
            for observations, moment in [read_snapshot(path)]]
