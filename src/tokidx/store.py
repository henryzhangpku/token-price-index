"""Bitemporal storage. Two clocks, kept apart on purpose.

*event time* (``observed_at``, ``index_date``)
    When the price was true in the market.
*knowledge time* (``collected_at``, ``published_at``)
    When this system first knew it.

The distinction is what makes a series defensible. "What was TIX-K26-OUT on
8 September?" and "what did we *say* it was on 8 September, as known on the
morning of the 9th?" are different questions, and a settlement agent resolving
a dispute months later can only use the second. A store that overwrites in
place can answer the first and never the second.

So corrections append. A restated value supersedes its predecessor and is
stamped as the live one; the predecessor stays exactly as published, because
somebody may have settled against it and a record you can rewrite is not a
record.

One note on the schema that is a measurement rather than a preference. The
as-of query filters on two equality predicates and one range, then takes the
newest revision. The obvious index -- ``(index_code, index_date,
published_at)`` -- serves the range seek but leaves ``revision`` unsorted, so
SQLite builds a temporary B-tree to order it. Putting ``revision`` third
instead lets the planner walk the index backwards and stop at the first row
that passes the ``published_at`` filter, which is almost always the first row
it reads. ``tests/test_store.py`` asserts the plan, so a future schema change
that reintroduces the sort fails the suite rather than passing quietly.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

from .models import Fixing
from .spec import METHODOLOGY_VERSION

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "tokidx.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS collection_runs (
    run_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    collected_at        TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    observation_count   INTEGER NOT NULL,
    source_summary      TEXT NOT NULL
);

-- Append-only. A correction inserts a new revision and stamps the previous
-- one with superseded_at; nothing is ever updated in place except that stamp.
CREATE TABLE IF NOT EXISTS fixings (
    index_code          TEXT NOT NULL,
    index_date          TEXT NOT NULL,
    revision            INTEGER NOT NULL,
    status              TEXT NOT NULL,
    value               REAL,
    dispersion          REAL,
    provider_count      INTEGER NOT NULL,
    observation_count   INTEGER NOT NULL,
    withheld_reason     TEXT,
    methodology_version TEXT NOT NULL,
    published_at        TEXT NOT NULL,
    superseded_at       TEXT,
    revision_reason     TEXT,
    run_id              INTEGER REFERENCES collection_runs(run_id),
    PRIMARY KEY (index_code, index_date, revision)
);

-- revision third, not published_at: see the module docstring. The plan is
-- asserted in tests/test_store.py.
CREATE INDEX IF NOT EXISTS idx_fixings_asof
    ON fixings(index_code, index_date, revision);

-- The per-seller audit trail behind one published value.
CREATE TABLE IF NOT EXISTS contributions (
    index_code          TEXT NOT NULL,
    index_date          TEXT NOT NULL,
    revision            INTEGER NOT NULL,
    provider            TEXT NOT NULL,
    price               REAL NOT NULL,
    weight              REAL NOT NULL,
    quote_count         INTEGER NOT NULL,
    tier                INTEGER NOT NULL,
    screened_out        INTEGER NOT NULL,
    screen_reason       TEXT,
    PRIMARY KEY (index_code, index_date, revision, provider)
);

CREATE TABLE IF NOT EXISTS flags (
    flag_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              INTEGER REFERENCES collection_runs(run_id),
    index_code          TEXT,
    index_date          TEXT,
    severity            TEXT NOT NULL,
    code                TEXT NOT NULL,
    detail              TEXT NOT NULL,
    raised_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_flags_run ON flags(run_id);
"""


def _iso_z(moment: datetime) -> str:
    """One spelling for every timestamp in the database.

    Timestamps are compared as text by the as-of query, so a second spelling
    would sort wrongly against the first and silently return the wrong
    revision. Cheaper to canonicalise on the way in than to discover later.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class Store:
    """Append-only bitemporal storage for fixings and their inputs."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else DEFAULT_DB
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- writing ------------------------------------------------------------

    def start_run(self, collected_at: datetime, observation_count: int,
                  source_summary: dict) -> int:
        cursor = self.conn.execute(
            "INSERT INTO collection_runs"
            " (collected_at, methodology_version, observation_count, source_summary)"
            " VALUES (?, ?, ?, ?)",
            (_iso_z(collected_at), METHODOLOGY_VERSION, observation_count,
             json.dumps(source_summary, sort_keys=True)),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def record(
        self,
        fixing: Fixing,
        *,
        published_at: datetime | None = None,
        run_id: int | None = None,
        revision_reason: str | None = None,
    ) -> int:
        """Write a fixing as a new revision. Returns the revision number.

        The first write for a date is revision 1 and needs no reason. Every
        later one supersedes its predecessor and must say why -- a restatement
        without a reason is indistinguishable from a bug, and makes the
        history unreadable by the only people who would ever need it.
        """
        moment = _iso_z(published_at or datetime.now(UTC))
        day = fixing.index_date.isoformat()

        previous = self.conn.execute(
            "SELECT MAX(revision) AS r FROM fixings"
            " WHERE index_code = ? AND index_date = ?",
            (fixing.index_code, day),
        ).fetchone()["r"]
        revision = (previous or 0) + 1

        if revision > 1:
            if not revision_reason:
                raise ValueError(
                    "a restatement must carry a reason; revision "
                    f"{revision} of {fixing.index_code} {day} has none"
                )
            self.conn.execute(
                "UPDATE fixings SET superseded_at = ?"
                " WHERE index_code = ? AND index_date = ? AND revision = ?",
                (moment, fixing.index_code, day, previous),
            )

        self.conn.execute(
            "INSERT INTO fixings (index_code, index_date, revision, status, value,"
            " dispersion, provider_count, observation_count, withheld_reason,"
            " methodology_version, published_at, superseded_at, revision_reason, run_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
            (
                fixing.index_code,
                day,
                revision,
                "published" if fixing.published else "withheld",
                fixing.value if fixing.published else None,
                fixing.dispersion,
                len(fixing.contributing),
                sum(p.quote_count for p in fixing.contributing),
                fixing.withheld_reason,
                fixing.methodology_version or METHODOLOGY_VERSION,
                moment,
                revision_reason,
                run_id,
            ),
        )

        for point in fixing.providers:
            self.conn.execute(
                "INSERT INTO contributions (index_code, index_date, revision, provider,"
                " price, weight, quote_count, tier, screened_out, screen_reason)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    fixing.index_code, day, revision, point.provider,
                    point.price, point.weight, point.quote_count,
                    int(point.best_tier), int(point.screened_out), point.screen_reason,
                ),
            )

        for flag in fixing.flags:
            self.conn.execute(
                "INSERT INTO flags (run_id, index_code, index_date, severity, code,"
                " detail, raised_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, fixing.index_code, day, flag.severity, flag.code,
                 flag.detail, moment),
            )

        self.conn.commit()
        return revision

    def restore(self, row: dict) -> None:
        """Load one tape row verbatim, revision numbering intact.

        ``record`` numbers revisions itself and demands a reason for every
        restatement. A rebuild is not a restatement -- it is the tape being
        read back -- so the row goes in exactly as published, and a row that
        is already present is replaced by its own copy rather than duplicated.
        Contributions and flags are not on the tape and are not restored;
        ``explain`` derives the breakdown from the snapshot instead.
        """
        key = (row["index_code"], row["index_date"], int(row["revision"]))
        self.conn.execute(
            "DELETE FROM fixings WHERE index_code = ? AND index_date = ? AND revision = ?",
            key,
        )
        self.conn.execute(
            "INSERT INTO fixings (index_code, index_date, revision, status, value,"
            " dispersion, provider_count, observation_count, withheld_reason,"
            " methodology_version, published_at, superseded_at, revision_reason, run_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                *key,
                row["status"],
                float(row["value"]) if row.get("value") not in (None, "") else None,
                float(row["dispersion"]) if row.get("dispersion") not in (None, "") else None,
                int(row["provider_count"]),
                int(row["observation_count"]),
                row.get("withheld_reason") or None,
                row["methodology_version"],
                row["published_at"],
                row.get("superseded_at") or None,
                row.get("revision_reason") or None,
            ),
        )
        self.conn.commit()

    # -- reading ------------------------------------------------------------

    def as_of(
        self, index_code: str, index_date: date, knowledge_time: datetime
    ) -> sqlite3.Row | None:
        """What this system said for ``index_date``, as known at a moment.

        The query a dispute actually needs, and the reason the schema is
        append-only. Ordering by revision rather than by published_at is
        deliberate: revisions are issued in order, so the newest revision at
        or before a knowledge time is the newest revision whose published_at
        passes the filter.
        """
        return self.conn.execute(
            "SELECT * FROM fixings WHERE index_code = ? AND index_date = ?"
            " AND published_at <= ? ORDER BY revision DESC LIMIT 1",
            (index_code, index_date.isoformat(), _iso_z(knowledge_time)),
        ).fetchone()

    def latest(self, index_code: str, index_date: date) -> sqlite3.Row | None:
        """The live revision for one date, whatever we believe now."""
        return self.conn.execute(
            "SELECT * FROM fixings WHERE index_code = ? AND index_date = ?"
            " ORDER BY revision DESC LIMIT 1",
            (index_code, index_date.isoformat()),
        ).fetchone()

    def history(self, index_code: str, limit: int = 30) -> list[sqlite3.Row]:
        """The live revision for each date, newest date first.

        Greatest-n-per-group, written as a window function rather than as a
        correlated subquery. The subquery form re-executes once per candidate
        row; this makes one pass. SQLite has had window functions since 3.25,
        which ships with every Python this package supports.
        """
        return self.conn.execute(
            "SELECT * FROM ("
            "  SELECT *, ROW_NUMBER() OVER ("
            "      PARTITION BY index_code, index_date ORDER BY revision DESC"
            "  ) AS rn FROM fixings WHERE index_code = ?"
            ") WHERE rn = 1 ORDER BY index_date DESC LIMIT ?",
            (index_code, limit),
        ).fetchall()

    def revisions(self, index_code: str, index_date: date) -> list[sqlite3.Row]:
        """Every revision ever published for one date, oldest first."""
        return self.conn.execute(
            "SELECT * FROM fixings WHERE index_code = ? AND index_date = ?"
            " ORDER BY revision ASC",
            (index_code, index_date.isoformat()),
        ).fetchall()

    def contributions(
        self, index_code: str, index_date: date, revision: int
    ) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM contributions WHERE index_code = ? AND index_date = ?"
            " AND revision = ? ORDER BY price ASC",
            (index_code, index_date.isoformat(), revision),
        ).fetchall()

    def query_plan(self, sql: str, args: tuple) -> list[str]:
        """The planner's chosen path, so a test can assert on it."""
        return [row[-1] for row in self.conn.execute("EXPLAIN QUERY PLAN " + sql, args)]


@contextmanager
def open_store(path: Path | str | None = None) -> Iterator[Store]:
    store = Store(path)
    try:
        yield store
    finally:
        store.close()
