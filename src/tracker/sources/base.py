"""Source interface plus parsing helpers every source shares."""

import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import ClassVar, TypeVar

from tracker.domain import FetchResult, ItemQuery, RawListing
from tracker.log import get_logger

log = get_logger(__name__)
T = TypeVar("T")

_MONEY_RE = re.compile(r"\d+(?:\.\d+)?")


class ParseError(ValueError):
    """A source record did not have the expected shape (often a layout change)."""


def parse_money(text: str | float | int | None) -> Decimal:
    """Extract a price such as '£51.77', '1,299.99' or 12.5 as a Decimal."""
    if text is None:
        raise ParseError("price missing")
    if isinstance(text, int | float):
        return Decimal(str(text))
    match = _MONEY_RE.search(text.replace(",", ""))
    if not match:
        raise ParseError(f"no number in price text {text!r}")
    try:
        return Decimal(match.group())
    except InvalidOperation as exc:  # pragma: no cover - regex guarantees a number
        raise ParseError(str(exc)) from exc


def utcnow() -> datetime:
    """Timezone-aware current time (patchable in tests)."""
    return datetime.now(UTC)


class Source(ABC):
    """A place prices come from. Subclasses only implement `fetch`."""

    name: ClassVar[str]

    @abstractmethod
    def fetch(self, queries: Sequence[ItemQuery], now: datetime | None = None) -> FetchResult:
        """Return listings for the given queries. Must not raise for single bad records."""

    def _parse_all(
        self,
        result: FetchResult,
        records: Iterable[T],
        parse: Callable[[T], RawListing],
    ) -> None:
        """Parse records into `result`, counting failures instead of raising."""
        for record in records:
            result.attempted += 1
            try:
                result.listings.append(parse(record))
            except (ParseError, KeyError, TypeError) as exc:
                result.parse_failures += 1
                log.debug("record_parse_failed", source=self.name, error=str(exc))
