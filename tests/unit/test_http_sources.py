from collections.abc import Callable
from decimal import Decimal

import respx

from tracker.domain import ItemQuery
from tracker.sources.books_toscrape import BooksToScrapeSource
from tracker.sources.dummyjson import DummyJSONSource
from tracker.sources.http import PoliteClient

BOOK_URL = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"


@respx.mock
def test_dummyjson_search(fast_client: PoliteClient, fixture_text: Callable[[str], str]) -> None:
    respx.get("https://dummyjson.com/robots.txt").respond(404)
    respx.get("https://dummyjson.com/products/search").respond(
        200, text=fixture_text("dummyjson_search_phone.json")
    )
    result = DummyJSONSource(client=fast_client).fetch([ItemQuery(item_id="p", query="phone")])
    assert result.attempted == 3
    assert result.parse_failures == 1  # the record with no title
    first, second = result.listings
    assert first.price == Decimal("199.99")
    assert first.url == "https://dummyjson.com/products/121"
    assert second.in_stock is False


@respx.mock
def test_dummyjson_records_errors_per_query(fast_client: PoliteClient) -> None:
    respx.get("https://dummyjson.com/robots.txt").respond(404)
    respx.get("https://dummyjson.com/products/search").respond(400)
    result = DummyJSONSource(client=fast_client).fetch([ItemQuery(item_id="p", query="phone")])
    assert result.listings == []
    assert len(result.errors) == 1


@respx.mock
def test_books_product_page(fast_client: PoliteClient, fixture_text: Callable[[str], str]) -> None:
    respx.get("https://books.toscrape.com/robots.txt").respond(404)
    respx.get(BOOK_URL).respond(200, text=fixture_text("books_toscrape_product.html"))
    result = BooksToScrapeSource(client=fast_client).fetch([ItemQuery(item_id="b", query=BOOK_URL)])
    (listing,) = result.listings
    assert listing.title == "A Light in the Attic"
    assert listing.price == Decimal("51.77")
    assert listing.currency == "GBP"
    assert listing.source_listing_id == "a897fe39b1053632"
    assert listing.in_stock


@respx.mock
def test_books_layout_change_counts_as_parse_failure(fast_client: PoliteClient) -> None:
    respx.get("https://books.toscrape.com/robots.txt").respond(404)
    respx.get(BOOK_URL).respond(
        200, text="<html><body><div class='new-layout'></div></body></html>"
    )
    result = BooksToScrapeSource(client=fast_client).fetch([ItemQuery(item_id="b", query=BOOK_URL)])
    assert result.listings == []
    assert result.parse_failures == 1


def test_books_ignores_search_terms(fast_client: PoliteClient) -> None:
    result = BooksToScrapeSource(client=fast_client).fetch([ItemQuery(item_id="b", query="poetry")])
    assert result.attempted == 0
