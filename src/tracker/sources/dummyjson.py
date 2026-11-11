"""DummyJSON products API: a public, key-less JSON API with sample product data.

Its prices are static, so it demonstrates real HTTP ingestion rather than
interesting price history (the mock source covers that).
"""

import re
from collections.abc import Sequence
from datetime import datetime
from functools import partial
from typing import Any, ClassVar

from tracker.domain import FetchResult, ItemQuery, RawListing
from tracker.log import get_logger
from tracker.sources.base import ParseError, Source, parse_money, utcnow
from tracker.sources.http import PoliteClient

log = get_logger(__name__)
BASE_URL = "https://dummyjson.com"
_PRODUCT_URL_RE = re.compile(r"^https?://dummyjson\.com/products/(\d+)")


class DummyJSONSource(Source):
    """Search terms hit /products/search; product URLs hit /products/{id}."""

    name: ClassVar[str] = "dummyjson"

    def __init__(self, client: PoliteClient | None = None, search_limit: int = 5) -> None:
        self._client = client or PoliteClient(min_interval_s=0.5)
        self._search_limit = search_limit

    def fetch(self, queries: Sequence[ItemQuery], now: datetime | None = None) -> FetchResult:
        """Fetch every query, isolating failures per query."""
        result = FetchResult(source=self.name)
        for q in queries:
            try:
                records = self._records_for(q)
            except Exception as exc:
                log.error(
                    "source_query_failed", source=self.name, item_id=q.item_id, error=str(exc)
                )
                result.errors.append(f"{q.item_id}: {exc}")
                continue
            fetched_at = now or utcnow()
            self._parse_all(result, records, partial(self._parse, q=q, fetched_at=fetched_at))
        return result

    def _records_for(self, q: ItemQuery) -> list[dict[str, Any]]:
        """Call the right endpoint for a search term or a product URL."""
        if q.is_url:
            match = _PRODUCT_URL_RE.match(q.query)
            if not match:
                return []
            data: Any = self._client.get(f"{BASE_URL}/products/{match.group(1)}").json()
            return [data]
        params = {"q": q.query, "limit": self._search_limit}
        data = self._client.get(f"{BASE_URL}/products/search", params=params).json()
        products = data.get("products")
        if not isinstance(products, list):
            raise ParseError("response has no 'products' list")
        return products

    def _parse(self, record: dict[str, Any], q: ItemQuery, fetched_at: datetime) -> RawListing:
        """Map one DummyJSON product to a RawListing."""
        pid = record["id"]
        return RawListing(
            source=self.name,
            source_listing_id=str(pid),
            item_id=q.item_id,
            title=str(record["title"]),
            url=f"{BASE_URL}/products/{pid}",
            price=parse_money(record["price"]),
            currency="USD",
            in_stock=record.get("availabilityStatus", "In Stock") != "Out of Stock",
            fetched_at=fetched_at,
            raw={k: record.get(k) for k in ("brand", "category", "availabilityStatus")},
        )
