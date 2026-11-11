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


class Item(BaseModel):
    """A product the user tracks, with an optional target price."""

    item_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    query: str
    target_price: Decimal | None = Field(default=None, gt=0)
    currency: str = "USD"
    sources: list[str] = Field(default_factory=lambda: ["mock"], min_length=1)
    active: bool = True

    def to_query(self) -> ItemQuery:
        """The query a source needs for this item."""
        return ItemQuery(item_id=self.item_id, query=self.query)


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

    @property
    def listing_id(self) -> str:
        """Globally unique listing id: '<source>:<source_listing_id>'."""
        return f"{self.source}:{self.source_listing_id}"


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
