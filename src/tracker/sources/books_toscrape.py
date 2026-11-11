"""HTML scraper for books.toscrape.com, a sandbox site built for scraping practice.

It exists to show the requests + BeautifulSoup path (robots.txt, rate limit,
layout-change detection). Only product URLs are supported; the site has no search.
"""

from collections.abc import Sequence
from datetime import datetime
from functools import partial
from typing import ClassVar
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from tracker.domain import FetchResult, ItemQuery, RawListing
from tracker.log import get_logger
from tracker.sources.base import ParseError, Source, parse_money, utcnow
from tracker.sources.http import PoliteClient

log = get_logger(__name__)
HOST = "books.toscrape.com"
PARSER_VERSION = "1"


class BooksToScrapeSource(Source):
    """Scrape one product page per URL query."""

    name: ClassVar[str] = "books_toscrape"

    def __init__(self, client: PoliteClient | None = None) -> None:
        self._client = client or PoliteClient(min_interval_s=1.0)

    def fetch(self, queries: Sequence[ItemQuery], now: datetime | None = None) -> FetchResult:
        """Fetch and parse each product page."""
        result = FetchResult(source=self.name)
        for q in queries:
            if not q.is_url or urlsplit(q.query).netloc != HOST:
                continue
            try:
                html = self._client.get(q.query).text
            except Exception as exc:
                log.error(
                    "source_query_failed", source=self.name, item_id=q.item_id, error=str(exc)
                )
                result.errors.append(f"{q.item_id}: {exc}")
                continue
            fetched_at = now or utcnow()
            self._parse_all(result, [html], partial(parse_product_page, q=q, fetched_at=fetched_at))
        return result


def parse_product_page(html: str, q: ItemQuery, fetched_at: datetime) -> RawListing:
    """Parse a books.toscrape product page. Raises ParseError if the layout changed."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one("div.product_main h1")
    price = soup.select_one("div.product_main p.price_color")
    availability = soup.select_one("div.product_main p.availability")
    if title is None or price is None or availability is None:
        raise ParseError("expected product_main title/price/availability elements")
    info = {
        th.get_text(strip=True): td.get_text(strip=True)
        for row in soup.select("table.table tr")
        if (th := row.find("th")) is not None and (td := row.find("td")) is not None
    }
    if "UPC" not in info:
        raise ParseError("product information table has no UPC")
    return RawListing(
        source=BooksToScrapeSource.name,
        source_listing_id=info["UPC"],
        item_id=q.item_id,
        title=title.get_text(strip=True),
        url=q.query,
        price=parse_money(price.get_text()),
        currency="GBP",
        in_stock="in stock" in availability.get_text().lower(),
        fetched_at=fetched_at,
        raw={"parser_version": PARSER_VERSION, "availability": availability.get_text(strip=True)},
    )
