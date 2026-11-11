"""Core data types shared across ingestion, storage and alerts."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ItemQuery(BaseModel):
    """One thing the user tracks: a search term or a product URL."""

    model_config = ConfigDict(frozen=True)

    item_id: str
    query: str

    @property
    def is_url(self) -> bool:
        """True when the query is a product URL rather than a search term."""
        return self.query.startswith(("http://", "https://"))


class RawListing(BaseModel):
    """A single price observation as parsed from a source, before validation."""

    model_config = ConfigDict(frozen=True)

    source: str
    source_listing_id: str
    item_id: str
    title: str
    url: str
    price: Decimal | None
    shipping: Decimal = Decimal("0")
    currency: str = "USD"
    in_stock: bool = True
    fetched_at: datetime
    raw: dict[str, Any] = Field(default_factory=dict)


class FetchResult(BaseModel):
    """Everything one source returned for one run, including parse failures."""

    source: str
    listings: list[RawListing] = Field(default_factory=list)
    attempted: int = 0
    parse_failures: int = 0
    errors: list[str] = Field(default_factory=list)


class RejectReason(StrEnum):
    """Why a parsed observation was refused by the validator."""

    MISSING_PRICE = "missing_price"
    NON_POSITIVE = "non_positive_price"
    OUTLIER = "price_outlier"
    ABSURD = "absurd_price"
    BAD_CURRENCY = "bad_currency"


class Rejection(BaseModel):
    """A refused observation, kept for data-quality reporting."""

    listing: RawListing
    reason: RejectReason
    detail: str
