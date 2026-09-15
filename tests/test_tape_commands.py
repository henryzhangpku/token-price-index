"""The tape is written by the commands a benchmark administrator actually runs.

The store was built and tested before anything in the operator path wrote to
it, so the README described a bitemporal record that ``publish`` never
touched. These pin the wiring: ``publish`` appends to the tape and refuses to
manufacture a second revision without a reason, ``rebuild`` restores the tape
with revision numbers intact and backfills only what the tape never saw, and
``as-of`` answers the question a dispute asks -- what did we say, as known when.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from typer.testing import CliRunner

from tokidx import cli
from tokidx.archive import read_tape
from tokidx.sources import collection_dates
from tokidx.spec import CONTRACTS
from tokidx.store import Store

WIDE = {"COLUMNS": "200", "TERM": "dumb", "PYTHONIOENCODING": "utf-8"}


def _run(*args: str):
    return CliRunner().invoke(cli.app, list(args), env=WIDE)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "tape.db"


@pytest.fixture
def tape(tmp_path):
    return tmp_path / "tape.csv"


def _day(iso: str):
    return datetime.fromisoformat(iso).date()


def test_publish_appends_one_tape_row_per_contract(db, tape):
    result = _run("publish", "--db", str(db), "--tape", str(tape))
    assert result.exit_code == 0, result.output
    rows = read_tape(tape)
    assert {r["index_code"] for r in rows} == set(CONTRACTS)
    assert {r["index_date"] for r in rows} == {collection_dates()[-1]}
    assert all(r["revision"] == "1" for r in rows)
    assert all(r["snapshot"] == f"{collection_dates()[-1]}.json" for r in rows)


def test_publish_refuses_a_silent_restatement(db, tape):
    _run("publish", "--db", str(db), "--tape", str(tape))
    again = _run("publish", "--db", str(db), "--tape", str(tape))
    assert again.exit_code == 0, again.output
    assert "already on file" in again.output
    assert len(read_tape(tape)) == len(CONTRACTS)


def test_publish_with_a_reason_writes_a_second_revision_and_stamps_the_first(db, tape):
    _run("publish", "--db", str(db), "--tape", str(tape))
    restated = _run("publish", "--db", str(db), "--tape", str(tape), "--reason", "test")
    assert restated.exit_code == 0, restated.output
    assert "as a restatement" in restated.output

    code, day = next(iter(CONTRACTS)), collection_dates()[-1]
    mine = [r for r in read_tape(tape) if r["index_code"] == code]
    assert [r["revision"] for r in mine] == ["1", "2"]
    assert mine[0]["superseded_at"] == mine[1]["published_at"]
    assert mine[1]["superseded_at"] == ""
    assert mine[1]["revision_reason"] == "test"

    listing = _run("revisions", code, day, "--db", str(db))
    assert listing.exit_code == 0, listing.output
    assert "test" in listing.output and "live" in listing.output


def test_rebuild_backfills_every_snapshot_date_the_tape_never_saw(db, tape):
    result = _run("rebuild", "--db", str(db), "--tape", str(tape))
    assert result.exit_code == 0, result.output
    expected = len(CONTRACTS) * len(collection_dates())
    assert f"backfilled  {expected}" in result.output
    rows = read_tape(tape)
    assert len(rows) == expected
    assert all("backfilled by rebuild" in r["revision_reason"] for r in rows)
    with Store(db) as store:
        for code in CONTRACTS:
            for day in collection_dates():
                assert [r["revision"] for r in store.revisions(code, _day(day))] == [1]


def test_rebuild_is_idempotent(db, tape):
    _run("rebuild", "--db", str(db), "--tape", str(tape))
    before = read_tape(tape)
    second = _run("rebuild", "--db", str(db), "--tape", str(tape))
    assert second.exit_code == 0, second.output
    assert "backfilled  0" in second.output
    assert read_tape(tape) == before


def test_rebuild_restores_revisions_verbatim_rather_than_recomputing(db, tape, tmp_path):
    """A restated value must survive a rebuild with its number and reason."""
    _run("publish", "--db", str(db), "--tape", str(tape))
    _run("publish", "--db", str(db), "--tape", str(tape), "--reason", "corrected")

    fresh = tmp_path / "fresh.db"
    result = _run("rebuild", "--db", str(fresh), "--tape", str(tape))
    assert result.exit_code == 0, result.output
    code, day = next(iter(CONTRACTS)), collection_dates()[-1]
    with Store(fresh) as store:
        rows = store.revisions(code, _day(day))
        assert [r["revision"] for r in rows] == [1, 2]
        assert rows[1]["revision_reason"] == "corrected"
        assert rows[0]["superseded_at"] is not None


def test_as_of_answers_what_was_known_when(db, tape):
    _run("publish", "--db", str(db), "--tape", str(tape))
    code, day = "TIX-GLM53-OUT", collection_dates()[-1]
    with Store(db) as store:
        published_at = store.revisions(code, _day(day))[0]["published_at"]
    moment = datetime.fromisoformat(published_at.replace("Z", "+00:00"))

    before = _run("as-of", code, day, (moment - timedelta(seconds=1)).isoformat(), "--db", str(db))
    assert before.exit_code == 1
    assert "not yet on the tape" in before.output

    after = _run("as-of", code, day, (moment + timedelta(days=1)).isoformat(), "--db", str(db))
    assert after.exit_code == 0, after.output
    assert "revision   1" in after.output
    assert "still the live value" in after.output


def test_a_backfilled_date_was_not_known_on_the_day(db, tape):
    """Backfilling must not rewrite history: the value became known at the
    rebuild, and as-of on the day itself says so."""
    _run("rebuild", "--db", str(db), "--tape", str(tape))
    code, day = "TIX-GLM53-OUT", collection_dates()[0]
    on_the_day = _run("as-of", code, day, f"{day}T23:59:59+00:00", "--db", str(db))
    assert on_the_day.exit_code == 1


def test_show_prints_the_series(db, tape):
    _run("rebuild", "--db", str(db), "--tape", str(tape))
    result = _run("show", "TIX-GLM53-OUT", "--db", str(db))
    assert result.exit_code == 0, result.output
    for day in collection_dates():
        assert day in result.output


def test_show_refuses_an_unknown_code(db):
    assert _run("show", "TIX-NOPE", "--db", str(db)).exit_code == 2


def test_revisions_of_an_unknown_date_exit_nonzero(db):
    assert _run("revisions", "TIX-GLM53-OUT", "1999-01-01", "--db", str(db)).exit_code == 1
