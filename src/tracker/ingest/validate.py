"""Reject impossible prices before they reach storage."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from tracker.domain import RawListing, Rejection, RejectReason

OUTLIER_FACTOR = Decimal(10)  # catches 100x spikes and 1/100 typos with margin
MAX_PRICE = Decimal(1_000_000)
ReferenceLookup = Callable[[RawListing], Decimal | None]


def check(listing: RawListing, reference_price: Decimal | None) -> Rejection | None:
    """Return a Rejection if the price is impossible, else None.

    `reference_price` is the listing's recent typical (median) price; without
    one, only the absolute checks run.
    """
    price = listing.price
    if price is None:
        return Rejection(listing=listing, reason=RejectReason.MISSING_PRICE, detail="no price")
    if price <= 0:
        return Rejection(listing=listing, reason=RejectReason.NON_POSITIVE, detail=str(price))
    if price > MAX_PRICE:
        return Rejection(listing=listing, reason=RejectReason.ABSURD, detail=str(price))
    if len(listing.currency) != 3 or not listing.currency.isalpha():
        return Rejection(listing=listing, reason=RejectReason.BAD_CURRENCY, detail=listing.currency)
    if reference_price and reference_price > 0:
        ratio = price / reference_price
        if ratio > OUTLIER_FACTOR or ratio < 1 / OUTLIER_FACTOR:
            detail = f"{price} vs typical {reference_price} ({ratio:.1f}x)"
            return Rejection(listing=listing, reason=RejectReason.OUTLIER, detail=detail)
    return None


@dataclass
class ValidationReport:
    """Accepted rows plus rejections, with counts per reason for logging."""

    accepted: list[RawListing] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        """Number of rejections per reason."""
        out: dict[str, int] = {}
        for r in self.rejected:
            out[r.reason.value] = out.get(r.reason.value, 0) + 1
        return out


def validate_all(listings: Iterable[RawListing], reference: ReferenceLookup) -> ValidationReport:
    """Split listings into accepted and rejected."""
    report = ValidationReport()
    for listing in listings:
        rejection = check(listing, reference(listing))
        if rejection is None:
            report.accepted.append(listing)
        else:
            report.rejected.append(rejection)
    return report
