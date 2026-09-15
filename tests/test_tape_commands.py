"""The tape is written by the commands a benchmark administrator actually runs.

The store was built and tested before anything in the operator path wrote to
it, so the README described a bitemporal record that ``publish`` never
touched. These pin the wiring: ``rebuild`` restores one revision per contract
per snapshot and is idempotent, ``publish`` records once and refuses to
manufacture a second revision without a reason, and ``as-of`` answers the
question a dispute asks -- what did we say, as known when.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from typer.testing import CliRunner

from tokidx import cli
from tokidx.sources import collection_dates
from tokidx.spec import CONTRACTS
from tokidx.store import Store

WIDE = {"COLUMNS": "200", "TERM": "dumb", "PYTHONIOENCODING": "utf-8"}


def _run(*args: str):
    return CliRunner().invoke(cli.app, list(args), env=WIDE)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "tape.db"


def test_rebuild_writes_one_revision_per_contract_per_snapshot(db):
    result = _run("rebuild", "--db", str(db))
    assert result.exit_code == 0, result.output
    with Store(db) as store:
        for code in CONTRACTS:
            for day in collection_dates():
                rows = store.revisions(code, datetime.fromisoformat(day).date())
                assert [r["revision"] for r in rows] == [1], (code, day)


def test_rebuild_is_idempotent(db):
    _run("rebuild", "--db", str(db))
    second = _run("rebuild", "--db", str(db))
    assert second.exit_code == 0, second.output
    assert "0 fixing(s) written" in second.output
    with Store(db) as store:
        code, day = next(iter(CONTRACTS)), collection_dates()[-1]
        assert len(store.revisions(code, datetime.fromisoformat(day).date())) == 1


def test_publish_records_once_and_refuses_a_silent_restatement(db):
    first = _run("publish", "--db", str(db))
    assert first.exit_code == 0, first.output
    assert f"{len(CONTRACTS)} fixing(s) recorded" in first.output

    again = _run("publish", "--db", str(db))
    assert again.exit_code == 0, again.output
    assert "already on file" in again.output
    assert "recorded" not in again.output

    with Store(db) as store:
        code, day = next(iter(CONTRACTS)), collection_dates()[-1]
        assert len(store.revisions(code, datetime.fromisoformat(day).date())) == 1


def test_publish_with_a_reason_writes_a_second_revision(db):
    _run("publish", "--db", str(db))
    restated = _run("publish", "--db", str(db), "--reason", "test restatement")
    assert restated.exit_code == 0, restated.output
    assert "as a restatement" in restated.output

    code, day = next(iter(CONTRACTS)), collection_dates()[-1]
    listing = _run("revisions", code, day, "--db", str(db))
    assert listing.exit_code == 0, listing.output
    assert "test restatement" in listing.output
    assert "live" in listing.output
    with Store(db) as store:
        rows = store.revisions(code, datetime.fromisoformat(day).date())
        assert [r["revision"] for r in rows] == [1, 2]
        assert rows[0]["superseded_at"] is not None
        assert rows[1]["superseded_at"] is None


def test_as_of_answers_what_was_known_when(db):
    _run("rebuild", "--db", str(db))
    code, day = "TIX-GLM53-OUT", collection_dates()[-1]
    with Store(db) as store:
        published_at = store.revisions(code, datetime.fromisoformat(day).date())[0]["published_at"]
    moment = datetime.fromisoformat(published_at.replace("Z", "+00:00"))

    before = _run("as-of", code, day, (moment - timedelta(seconds=1)).isoformat(), "--db", str(db))
    assert before.exit_code == 1
    assert "not yet on the tape" in before.output

    after = _run("as-of", code, day, (moment + timedelta(days=1)).isoformat(), "--db", str(db))
    assert after.exit_code == 0, after.output
    assert "revision   1" in after.output
    assert "still the live value" in after.output


def test_revisions_of_an_unknown_date_exit_nonzero(db):
    result = _run("revisions", "TIX-GLM53-OUT", "1999-01-01", "--db", str(db))
    assert result.exit_code == 1


def test_rebuild_stamps_each_fixing_with_its_snapshots_moment(db):
    """A rebuilt tape must not say every past fixing was published today."""
    _run("rebuild", "--db", str(db))
    with Store(db) as store:
        for day in collection_dates():
            row = store.revisions("TIX-GLM53-OUT", datetime.fromisoformat(day).date())[0]
            assert row["published_at"].startswith(day), row["published_at"]
