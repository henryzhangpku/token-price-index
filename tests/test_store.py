"""The store has to answer a question nobody asks until there is a dispute.

"What is TIX-K26-OUT for 8 September" is answerable by any table. "What did we
*say* it was on 8 September, as known on the morning of the 9th" is only
answerable by a store that never overwrote the first answer, and it is the
only one that helps a settlement agent months later.

So these tests are mostly about the second question, and about the one thing
that would silently break it: two spellings of a timestamp, compared as text.

The last test asserts a query plan. That is unusual in a unit suite and it is
deliberate -- the index column order was chosen to avoid a sort, and a future
schema change that reintroduces one should fail here rather than pass quietly
and get discovered under load.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from tokidx.models import Fixing, GateResult, ProviderPoint
from tokidx.spec import Tier
from tokidx.store import Store

DAY = date(2026, 9, 8)
MORNING = datetime(2026, 9, 8, 9, 0, tzinfo=UTC)
EVENING = datetime(2026, 9, 8, 21, 0, tzinfo=UTC)
NEXT_DAY = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)


def fixing(value: float | None = 4.0, *, day: date = DAY, published: bool = True) -> Fixing:
    gate = GateResult(name="min_providers", passed=published, detail="3 of 3 required")
    return Fixing(
        index_code="TIX-K26-OUT",
        index_date=day,
        value=value,
        dispersion=0.185,
        providers=[
            ProviderPoint(provider="deepinfra", price=3.50, quote_count=1,
                          best_tier=Tier.LIST, weight=1.0),
            ProviderPoint(provider="fireworks", price=4.00, quote_count=1,
                          best_tier=Tier.LIST, weight=1.0),
            ProviderPoint(provider="together", price=4.50, quote_count=1,
                          best_tier=Tier.LIST, weight=1.0),
        ],
        gates=[gate],
        methodology_version="0.1.0",
    )


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "t.db") as handle:
        yield handle


def test_a_revision_appends_and_never_overwrites(store):
    store.record(fixing(4.00), published_at=MORNING)
    store.record(fixing(3.90), published_at=NEXT_DAY, revision_reason="seller restated")
    rows = store.revisions("TIX-K26-OUT", DAY)
    assert [r["revision"] for r in rows] == [1, 2]
    assert [r["value"] for r in rows] == [4.00, 3.90]


def test_the_superseded_revision_is_stamped_but_not_changed(store):
    store.record(fixing(4.00), published_at=MORNING)
    store.record(fixing(3.90), published_at=NEXT_DAY, revision_reason="seller restated")
    first, second = store.revisions("TIX-K26-OUT", DAY)
    assert first["value"] == 4.00
    assert first["superseded_at"] is not None
    assert second["superseded_at"] is None


def test_a_restatement_without_a_reason_is_refused(store):
    """A correction nobody explained is indistinguishable from a bug."""
    store.record(fixing(4.00), published_at=MORNING)
    with pytest.raises(ValueError, match="must carry a reason"):
        store.record(fixing(3.90), published_at=NEXT_DAY)


def test_as_of_before_publication_returns_nothing(store):
    store.record(fixing(4.00), published_at=EVENING)
    assert store.as_of("TIX-K26-OUT", DAY, MORNING) is None


def test_as_of_at_the_exact_publication_instant_returns_the_value(store):
    """An off-by-one here makes the fixing invisible for the instant it exists."""
    store.record(fixing(4.00), published_at=EVENING)
    assert store.as_of("TIX-K26-OUT", DAY, EVENING)["value"] == 4.00


def test_as_of_between_revisions_returns_what_we_said_then(store):
    store.record(fixing(4.00), published_at=MORNING)
    store.record(fixing(3.90), published_at=NEXT_DAY, revision_reason="seller restated")
    assert store.as_of("TIX-K26-OUT", DAY, EVENING)["value"] == 4.00
    assert store.as_of("TIX-K26-OUT", DAY, NEXT_DAY)["value"] == 3.90


def test_latest_is_what_we_believe_now(store):
    store.record(fixing(4.00), published_at=MORNING)
    store.record(fixing(3.90), published_at=NEXT_DAY, revision_reason="seller restated")
    assert store.latest("TIX-K26-OUT", DAY)["value"] == 3.90


def test_history_returns_one_live_revision_per_date(store):
    store.record(fixing(4.00, day=date(2026, 9, 8)), published_at=MORNING)
    store.record(fixing(3.90, day=date(2026, 9, 8)), published_at=NEXT_DAY,
                 revision_reason="seller restated")
    store.record(fixing(4.10, day=date(2026, 9, 9)), published_at=NEXT_DAY)
    rows = store.history("TIX-K26-OUT")
    assert [r["index_date"] for r in rows] == ["2026-09-09", "2026-09-08"]
    assert [r["value"] for r in rows] == [4.10, 3.90]


def test_a_withheld_fixing_stores_no_value_but_keeps_its_reason(store):
    store.record(fixing(None, published=False), published_at=MORNING)
    row = store.latest("TIX-K26-OUT", DAY)
    assert row["status"] == "withheld"
    assert row["value"] is None
    assert "min_providers" in row["withheld_reason"]


def test_contributions_are_kept_for_every_revision(store):
    store.record(fixing(4.00), published_at=MORNING)
    rows = store.contributions("TIX-K26-OUT", DAY, 1)
    assert [r["provider"] for r in rows] == ["deepinfra", "fireworks", "together"]


def test_timestamps_have_one_spelling(store):
    """The as-of query compares text, so a second spelling sorts wrongly."""
    store.record(fixing(4.00), published_at=datetime(2026, 9, 8, 21, 0))  # naive
    store.record(fixing(3.90), published_at=NEXT_DAY, revision_reason="seller restated")
    for row in store.revisions("TIX-K26-OUT", DAY):
        assert row["published_at"].endswith("Z")
        assert "+00:00" not in row["published_at"]


def test_the_as_of_query_uses_the_index_and_does_not_sort(store):
    """The column order was chosen for this. A regression should fail here.

    Ordering by ``revision`` with the index ending in ``revision`` lets the
    planner walk backwards and stop at the first row that passes the
    ``published_at`` filter. Putting ``published_at`` third instead serves the
    range seek but forces a temporary B-tree for the ORDER BY.
    """
    store.record(fixing(4.00), published_at=MORNING)
    plan = store.query_plan(
        "SELECT * FROM fixings WHERE index_code = ? AND index_date = ?"
        " AND published_at <= ? ORDER BY revision DESC LIMIT 1",
        ("TIX-K26-OUT", DAY.isoformat(), "2026-09-08T21:00:00Z"),
    )
    joined = " ".join(plan)
    assert "idx_fixings_asof" in joined, joined
    assert "TEMP B-TREE" not in joined.upper(), joined
