from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tracker.domain import ItemQuery
from tracker.ingest.layout_guard import check_layout
from tracker.sources.mock import MockSource, event_at, hourly, search

T0 = datetime(2026, 3, 1, 12, tzinfo=UTC)
HEADPHONES = ItemQuery(item_id="hp", query="headphones")


def test_search_matches_all_words() -> None:
    assert [p.sku for p in search("kinetix 256gb")] == ["KIN-K8-256"]
    assert {p.sku for p in search("kinetix")} == {"KIN-K8-128", "KIN-K8-256"}
    assert search("") == []
    assert search("nonexistent") == []


def test_same_product_is_listed_by_every_store() -> None:
    result = MockSource().fetch([HEADPHONES], now=T0)
    stores = {listing.raw["store"] for listing in result.listings}
    assert stores == {"shopmart", "bytebazaar", "dealdepot"}
    assert result.parse_failures == 0


def test_prices_are_deterministic() -> None:
    a = MockSource(seed=7).fetch([HEADPHONES], now=T0).listings
    b = MockSource(seed=7).fetch([HEADPHONES], now=T0).listings
    c = MockSource(seed=8).fetch([HEADPHONES], now=T0).listings
    assert [x.price for x in a] == [x.price for x in b]
    assert [x.price for x in a] != [x.price for x in c]


def test_history_contains_planted_anomalies_and_events() -> None:
    src = MockSource()
    prices: list[Decimal] = []
    for ts in hourly(T0, T0 + timedelta(days=180), step_hours=6):
        prices += [x.price for x in src.fetch([HEADPHONES], now=ts).listings if x.price is not None]
    assert prices.count(Decimal("0.00")) > 0  # $0 glitches
    assert max(prices) > 10_000  # 100x spikes on a ~$250 item
    kinds = Counter(
        e.kind
        for ts in hourly(T0, T0 + timedelta(days=180), step_hours=24)
        if (e := event_at(42, "shopmart:AUR-NC700", ts)) is not None
    )
    assert kinds["genuine_deal"] > 0


def test_layout_change_is_detected() -> None:
    ok = MockSource(layout_version=1).fetch([HEADPHONES], now=T0)
    broken = MockSource(layout_version=2).fetch([HEADPHONES], now=T0)
    assert not check_layout(ok).broken
    status = check_layout(broken)
    assert status.broken
    assert status.failure_rate == 1.0
    assert broken.listings == []
