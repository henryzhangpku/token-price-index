"""Snapshots are written once, read back exactly, and never overwritten."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tokidx.sources import SnapshotExists, read_snapshot, snapshot_paths, write_snapshot


def test_a_snapshot_round_trips_every_observation(tmp_path, make_obs):
    observations = [make_obs(provider=f"s{i}", price=0.1 * (i + 1)) for i in range(5)]
    moment = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    path = write_snapshot(observations, moment, directory=tmp_path)

    back, when = read_snapshot(path)
    assert when == moment
    assert back == observations


def test_a_second_collection_on_the_same_date_is_refused(tmp_path, make_obs):
    """A tape row may name this file. Overwriting it would silently replace
    the inputs behind a published value, which is what the archive exists to
    prevent."""
    moment = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    path = write_snapshot([make_obs(price=0.5)], moment, directory=tmp_path)
    original = path.read_text(encoding="utf-8")

    later = datetime(2026, 9, 8, 18, 0, tzinfo=UTC)
    with pytest.raises(SnapshotExists):
        write_snapshot([make_obs(price=9.9)], later, directory=tmp_path)

    assert path.read_text(encoding="utf-8") == original
    assert snapshot_paths(tmp_path) == [path]


def test_snapshots_list_oldest_first(tmp_path, make_obs):
    for day in (10, 8, 9):
        write_snapshot([make_obs()], datetime(2026, 9, day, tzinfo=UTC), directory=tmp_path)
    assert [p.stem for p in snapshot_paths(tmp_path)] == [
        "2026-09-08", "2026-09-09", "2026-09-10",
    ]
