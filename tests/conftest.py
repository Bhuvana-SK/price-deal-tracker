from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tracker.domain import RawListing
from tracker.sources.http import PoliteClient

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_text() -> Callable[[str], str]:
    """Read a file from tests/fixtures."""
    return lambda name: (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def sleeps() -> list[float]:
    """Collects sleep() calls so tests never actually wait."""
    return []


@pytest.fixture
def fast_client(sleeps: list[float]) -> PoliteClient:
    """A PoliteClient with real retry/rate-limit logic but a fake sleep."""
    return PoliteClient(
        min_interval_s=0.5, max_attempts=3, backoff_initial_s=0.01, sleep=sleeps.append
    )


def make_listing(**overrides: Any) -> RawListing:
    """A valid listing; override any field."""
    fields: dict[str, Any] = {
        "source": "mock",
        "source_listing_id": "shopmart:TEST",
        "item_id": "item-1",
        "title": "Test product",
        "url": "https://shopmart.example/p/test",
        "price": Decimal("100.00"),
        "fetched_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    fields.update(overrides)
    return RawListing(**fields)
