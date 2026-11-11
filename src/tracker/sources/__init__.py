"""Price sources. Use `get_source(name)` to build one."""

from tracker.sources.base import ParseError, Source
from tracker.sources.books_toscrape import BooksToScrapeSource
from tracker.sources.dummyjson import DummyJSONSource
from tracker.sources.mock import MockSource

SOURCE_NAMES = ("mock", "dummyjson", "books_toscrape")


def get_source(name: str) -> Source:
    """Build a source by name with default settings."""
    if name == "mock":
        return MockSource()
    if name == "dummyjson":
        return DummyJSONSource()
    if name == "books_toscrape":
        return BooksToScrapeSource()
    raise ValueError(f"unknown source {name!r}; expected one of {SOURCE_NAMES}")


__all__ = [
    "SOURCE_NAMES",
    "BooksToScrapeSource",
    "DummyJSONSource",
    "MockSource",
    "ParseError",
    "Source",
    "get_source",
]
