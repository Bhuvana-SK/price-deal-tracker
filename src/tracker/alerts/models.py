"""Types for alert rules, listing state and outgoing alerts."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class RuleType(StrEnum):
    """Kinds of alert rule."""

    BELOW_PRICE = "below_price"  # price <= threshold (the item's target price)
    DROP_24H = "pct_drop_24h"  # dropped >= threshold (fraction) in 24 hours
    LOW_90D = "low_90d"  # lowest price in 90 days
    GENUINE_DEAL = "genuine_deal"  # passes the dbt deal rule


class Rule(BaseModel):
    """One alert rule. `item_id=None` applies it to every item."""

    model_config = ConfigDict(frozen=True)

    rule_id: str
    rule_type: RuleType
    item_id: str | None = None
    threshold: Decimal | None = None


class ListingState(BaseModel):
    """A row of gold.mart_listing_current: what we know about a listing right now."""

    item_id: str
    item_name: str
    listing_id: str
    store: str
    title: str
    url: str
    currency: str
    current_price: Decimal
    current_total_cost: Decimal
    in_stock: bool
    as_of: datetime
    target_price: Decimal | None
    pct_drop_24h: float | None
    avg_30d: float | None
    min_90d: Decimal | None
    all_time_low: Decimal | None
    is_90d_low: bool | None
    has_enough_history: bool | None
    deal_flag: bool | None
    deal_reason: str | None


class Alert(BaseModel):
    """A notification ready to send. `subject_id` is an item_id or 'source:<name>'."""

    subject_id: str
    rule_id: str
    rule_type: str
    title: str
    body: str
    listing_id: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    url: str | None = None

    def text(self) -> str:
        """Plain-text rendering used by every channel."""
        link = f"\n{self.url}" if self.url else ""
        return f"{self.title}\n{self.body}{link}"
