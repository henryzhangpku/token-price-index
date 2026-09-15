from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from tokidx.models import Observation
from tokidx.normalize import normalize
from tokidx.spec import CONTRACTS, Direction, Serving, Tier

#: The contract every fixture targets unless told otherwise. Open weights,
#: many sellers -- the ordinary case. The refusal case has its own file.
CODE = "TIX-GLM53-OUT"


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """No test may write to the working store.

    ``publish`` records to the tape now, and a test that ran it against the
    real data/tokidx.db would leave a revision behind that the next real
    publish then refuses to overwrite without a reason.
    """
    import tokidx.store

    monkeypatch.setattr(tokidx.store, "DEFAULT_DB", tmp_path / "isolated.db")


@pytest.fixture
def day() -> date:
    return date(2026, 9, 8)


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


@pytest.fixture
def make_obs(now):
    """Build a benchmark-conforming observation with targeted overrides.

    Defaults are the contract as written, so a test that changes one field
    is testing exactly that field and nothing else.
    """

    def _make(
        provider: str = "seller-a",
        price: float = 0.50,
        model: str | None = None,
        direction: Direction = Direction.OUTPUT,
        serving: Serving = Serving.STANDARD,
        context_tokens: int = 128_000,
        region: str = "us",
        tier: Tier = Tier.LIST,
        note: str = "",
    ) -> Observation:
        return Observation(
            provider=provider,
            model=model or CONTRACTS[CODE].model,
            direction=direction,
            usd_per_mtok=price,
            serving=serving,
            context_tokens=context_tokens,
            region=region,
            tier=tier,
            observed_at=now,
            source_url=f"https://example.invalid/{provider}",
            note=note,
        )

    return _make


@pytest.fixture
def market(make_obs):
    """Quotes for a market where each seller lists a few rows around its level.

    Real sellers never publish exactly one row for one model -- context tiers
    and serving modes multiply the catalogue -- and a one-row-per-seller
    fixture would sit at the observation-count gate, testing the gate rather
    than the behaviour under test.
    """

    def _market(prices: dict[str, float], **kwargs):
        contract = CONTRACTS[CODE]
        quotes = []
        for provider, price in prices.items():
            for tilt in (0.98, 1.00, 1.02):
                q = normalize(make_obs(provider=provider, price=price * tilt, **kwargs), contract)
                quotes.append(q)
        return quotes

    return _market
