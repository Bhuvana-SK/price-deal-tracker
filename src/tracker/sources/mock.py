"""Deterministic mock source: realistic price history with no external accounts.

Prices are a pure function of (seed, listing, time), so any point in the past
can be "fetched" (fast backfills) and every run is reproducible. The generator
also plants things the pipeline must handle:

* genuine sales: a real drop below the usual price for a few days
* fake deals: price is raised for ~2 weeks, then "discounted" back to normal
* bad data: occasional $0 and 100x prices
* layout change: `layout_version=2` returns payloads the parser cannot read
"""

import hashlib
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from functools import cache, partial
from typing import Any, ClassVar, Literal

from tracker.domain import FetchResult, ItemQuery, RawListing
from tracker.sources.base import Source, parse_money, utcnow
from tracker.sources.mock_catalog import CATALOG, STORES, Product, Store

EPOCH = date(2025, 1, 1)
HORIZON_DAYS = 3 * 365
EventKind = Literal["genuine_deal", "inflated", "fake_deal"]


@dataclass(frozen=True)
class PriceEvent:
    """A span of days where price is shifted by `multiplier` relative to normal."""

    kind: EventKind
    start_day: int
    end_day: int  # exclusive
    multiplier: float


def _rng(*parts: object) -> random.Random:
    """A Random seeded from arbitrary parts (stable across processes, unlike hash())."""
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def _day_index(ts: datetime) -> int:
    return (ts.astimezone(UTC).date() - EPOCH).days


@cache
def events_for(seed: int, listing_id: str) -> tuple[PriceEvent, ...]:
    """Schedule sales and fake deals for one listing across the whole horizon."""
    rng = _rng(seed, listing_id, "events")
    events: list[PriceEvent] = []
    day = rng.randint(5, 30)
    while day < HORIZON_DAYS:
        if rng.random() < 0.7:
            length = rng.randint(2, 5)
            events.append(
                PriceEvent("genuine_deal", day, day + length, 1 - rng.uniform(0.15, 0.30))
            )
            day += length
        else:
            inflate = rng.randint(10, 14)
            events.append(PriceEvent("inflated", day, day + inflate, 1 + rng.uniform(0.25, 0.40)))
            events.append(PriceEvent("fake_deal", day + inflate, day + inflate + 3, 0.97))
            day += inflate + 3
        day += rng.randint(20, 45)
    return tuple(events)


def event_at(seed: int, listing_id: str, ts: datetime) -> PriceEvent | None:
    """The planted event active at `ts`, if any (ground truth for the backtest)."""
    d = _day_index(ts)
    return next((e for e in events_for(seed, listing_id) if e.start_day <= d < e.end_day), None)


def _to_retail(value: float) -> Decimal:
    """Round to a shop-style price: 249.99, 12.49 ..."""
    if value >= 20:
        return Decimal(math.floor(value)) + Decimal("0.99")
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def price_at(seed: int, product: Product, store: Store, ts: datetime) -> Decimal:
    """The listing's price at time `ts`, including planted anomalies."""
    listing_id = f"{store.code}:{product.sku}"
    d = _day_index(ts)
    phase = _rng(seed, listing_id, "phase").uniform(0, 2 * math.pi)
    drift = 0.04 * math.sin(2 * math.pi * d / 60 + phase)
    noise = _rng(seed, listing_id, d).gauss(0, 0.012)
    value = product.base_price * store.price_multiplier * (1 + drift + noise)
    event = event_at(seed, listing_id, ts)
    if event is not None:
        value *= event.multiplier
    anomaly = _rng(seed, listing_id, ts.strftime("%Y%m%d%H")).random()
    if anomaly < 0.004:
        return Decimal("0.00")
    if anomaly < 0.008:
        value *= 100
    return _to_retail(value)


def shipping_for(store: Store, price: Decimal) -> Decimal:
    """Store shipping policy."""
    if store.free_shipping_over is not None and price >= store.free_shipping_over:
        return Decimal("0")
    return store.flat_shipping


class MockSource(Source):
    """Searches the synthetic catalogue; every product is sold by several stores."""

    name: ClassVar[str] = "mock"
    supports_backfill: ClassVar[bool] = True

    def __init__(self, seed: int = 42, layout_version: int = 1) -> None:
        self.seed = seed
        self.layout_version = layout_version

    def fetch(self, queries: Sequence[ItemQuery], now: datetime | None = None) -> FetchResult:
        """Return one payload per (matching product, store), then parse it."""
        ts = now or utcnow()
        result = FetchResult(source=self.name)
        for q in queries:
            payloads = [self._payload(p, s, ts) for p in search(q.query) for s in STORES.values()]
            self._parse_all(result, payloads, partial(self._parse, q=q, fetched_at=ts))
        return result

    def _payload(self, product: Product, store: Store, ts: datetime) -> dict[str, Any]:
        """What the fake store 'API' returns. Version 2 simulates a breaking layout change."""
        price = price_at(self.seed, product, store, ts)
        sku = f"{store.code}:{product.sku}"
        in_stock = _rng(self.seed, sku, "stock", ts.date()).random() > 0.03
        if self.layout_version == 2:
            return {
                "id": sku,
                "productName": product.titles[store.code],
                "pricing": {"amountCents": int(price * 100)},
            }
        return {
            "sku": sku,
            "name": product.titles[store.code],
            "price": str(price),
            "shipping": str(shipping_for(store, price)),
            "currency": "USD",
            "stock": "in_stock" if in_stock else "out_of_stock",
            "link": f"https://{store.code}.example/p/{product.sku.lower()}",
        }

    def _parse(self, record: dict[str, Any], q: ItemQuery, fetched_at: datetime) -> RawListing:
        """Parse a v1 payload (raises KeyError on the v2 layout)."""
        return RawListing(
            source=self.name,
            source_listing_id=record["sku"],
            item_id=q.item_id,
            title=record["name"],
            url=record["link"],
            price=parse_money(record["price"]),
            shipping=parse_money(record["shipping"]),
            currency=record["currency"],
            in_stock=record["stock"] == "in_stock",
            fetched_at=fetched_at,
            raw={"store": record["sku"].split(":")[0]},
        )


def search(query: str) -> list[Product]:
    """Products whose keywords contain every word of the query."""
    words = set(query.lower().split())
    return [p for p in CATALOG if words and words <= p.keywords]


def hourly(start: datetime, end: datetime, step_hours: int = 6) -> list[datetime]:
    """Timestamps from start to end (inclusive) every `step_hours`; used for backfills."""
    out, ts = [], start
    while ts <= end:
        out.append(ts)
        ts += timedelta(hours=step_hours)
    return out
