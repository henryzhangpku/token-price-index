"""The published series must reproduce from its own archive.

A published value is a claim; the snapshot it names is the evidence. These
tests pin that the claim can be re-derived from the evidence, that tampering
with the evidence is detected, that a value whose evidence is missing is
reported as unverifiable rather than passing, and that revisions survive a
rebuild with their history intact.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from conftest import CODE

from tokidx.archive import append_to_tape, live_tape_values, read_tape, stamp_superseded
from tokidx.reproduce import TOLERANCE, rebuild, verify
from tokidx.sources import SNAPSHOT_DIR
from tokidx.spec import METHODOLOGY_VERSION
from tokidx.store import Store


@pytest.fixture
def archive(tmp_path):
    """A private copy of the real archive: snapshots, and a tape built from them."""
    snapshots = tmp_path / "observations"
    shutil.copytree(SNAPSHOT_DIR, snapshots)
    tape = tmp_path / "tape.csv"
    with Store(tmp_path / "a.db") as store:
        rebuild(store, tape, snapshots)
    return tape, snapshots


def test_the_series_reproduces_from_its_archive(archive):
    tape, snapshots = archive
    report = verify(tape, snapshots)
    assert report.ok, (report.mismatches, report.dangling, report.methodology_drift)
    assert report.checked == len(read_tape(tape))
    assert report.matched == report.checked


def test_tampering_with_a_snapshot_is_detected(archive):
    tape, snapshots = archive
    path = sorted(snapshots.glob("*.json"))[-1]
    payload = json.loads(path.read_text(encoding="utf-8"))
    for obs in payload["observations"]:
        if "glm" in obs["model"] and obs["direction"] == "output":
            obs["usd_per_mtok"] *= 3
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = verify(tape, snapshots)
    assert not report.ok
    assert any(m.index_code == CODE for m in report.mismatches)


def test_a_missing_snapshot_is_unverifiable_not_a_pass(archive):
    tape, snapshots = archive
    sorted(snapshots.glob("*.json"))[0].unlink()
    report = verify(tape, snapshots)
    assert report.unverifiable, "a value with no inputs on file must not count as reproduced"
    assert report.dangling, "every revision naming the lost file must be reported"
    assert not report.ok


def test_a_methodology_change_is_reported_not_recomputed(archive):
    """Recomputing under different rules would explain the value under rules
    it was not produced by. The row is reported, and skipped."""
    tape, snapshots = archive
    rows = read_tape(tape)
    rows[0]["methodology_version"] = "0.0.1"
    tape.unlink()
    append_to_tape(rows, tape)
    report = verify(tape, snapshots)
    assert len(report.methodology_drift) == 1
    assert METHODOLOGY_VERSION in report.methodology_drift[0]
    assert not report.ok


def test_superseded_is_stamped_without_disturbing_prior_rows(tmp_path):
    tape = tmp_path / "t.csv"
    base = {
        "index_code": CODE, "status": "published", "provider_count": 3,
        "observation_count": 3, "dispersion": 0.1, "withheld_reason": "",
        "methodology_version": METHODOLOGY_VERSION, "superseded_at": "",
        "revision_reason": "", "snapshot": "x.json",
    }
    append_to_tape([
        dict(base, index_date="2026-09-01", revision=1, value=1.0, published_at="2026-09-01T10:00:00Z"),
        dict(base, index_date="2026-09-01", revision=2, value=1.1, published_at="2026-09-01T12:00:00Z",
             revision_reason="corrected"),
        dict(base, index_date="2026-09-02", revision=1, value=1.2, published_at="2026-09-02T10:00:00Z"),
    ], tape)
    stamp_superseded(tape)
    rows = read_tape(tape)
    assert rows[0]["superseded_at"] == "2026-09-01T12:00:00Z"
    assert rows[0]["value"] == "1.0"
    assert rows[1]["superseded_at"] == ""
    assert rows[2]["superseded_at"] == ""
    assert live_tape_values(tape)[(CODE, "2026-09-01")]["revision"] == "2"


def test_rebuild_restores_the_publication_record_verbatim(archive, tmp_path):
    tape, snapshots = archive
    rows = read_tape(tape)
    with Store(tmp_path / "b.db") as store:
        restored, backfilled = rebuild(store, tape, snapshots)
        assert (restored, backfilled) == (len(rows), 0)
        for row in rows:
            stored = store.revisions(row["index_code"], _date(row["index_date"]))
            assert [r["revision"] for r in stored] == [1]
            assert stored[0]["published_at"] == row["published_at"]


def test_tolerance_is_half_a_unit_in_the_last_published_place():
    """Two runs over the same inputs agree to the printed digit, or verify
    would report floating-point noise as a broken series."""
    assert pytest.approx(0.00005) == TOLERANCE


def _date(iso: str):
    from datetime import date

    return date.fromisoformat(iso)


def test_snapshot_dir_is_where_the_tape_expects(tmp_path):
    """The tape names snapshots by file name; verify resolves them next to it."""
    assert Path(SNAPSHOT_DIR).name == "observations"
