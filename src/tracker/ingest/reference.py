"""What is a listing's "typical" price? The reference the outlier check compares against.

Two failure modes this guards against (both found in practice):

* Cold start: a brand-new listing's first price is a 100x glitch. With no
  history, it would be accepted, become the reference, and every normal price
  after it would be rejected. So a listing with little history is compared
  against the *other stores selling the same item* in the same batch.
* Poisoned history: if a listing's reference is wrong anyway (e.g. a single-store
  item whose first price was bad), the listing would be stuck forever. So when
  several consecutive "outliers" agree with each other, the reference resets.
"""

import statistics
from collections import defaultdict, deque
from collections.abc import Sequence
from decimal import Decimal

from tracker.domain import RawListing, Rejection, RejectReason
from tracker.ingest.validate import ValidationReport, check
from tracker.log import get_logger

log = get_logger(__name__)
MIN_OWN_HISTORY = 3  # fewer accepted prices than this: trust peers over own history
MIN_PEERS = 3  # need at least this many prices for the item in a batch to use them
RESET_STREAK = 3  # this many consistent consecutive outliers means the reference is wrong
RESET_SPREAD = Decimal("1.5")  # "consistent": max/min of the streak within 1.5x


class ReferenceTracker:
    """Rolling median of each listing's recent accepted prices, with self-healing."""

    def __init__(self, history: dict[str, list[Decimal]], window: int = 20) -> None:
        self._window = window
        self._prices: dict[str, deque[Decimal]] = {
            lid: deque(p, maxlen=window) for lid, p in history.items()
        }
        self._outlier_streak: dict[str, list[Decimal]] = defaultdict(list)

    def own_reference(self, listing: RawListing) -> tuple[Decimal | None, int]:
        """(median of this listing's history, number of points)."""
        prices = self._prices.get(listing.listing_id)
        return (statistics.median(prices), len(prices)) if prices else (None, 0)

    def observe(self, listing: RawListing) -> None:
        """Add an accepted price and clear any outlier streak."""
        if listing.price is None:
            return
        self._prices.setdefault(listing.listing_id, deque(maxlen=self._window)).append(
            listing.price
        )
        self._outlier_streak.pop(listing.listing_id, None)

    def outlier_means_new_level(self, listing: RawListing) -> bool:
        """Record an outlier; True if the streak shows the reference itself is wrong."""
        if listing.price is None:
            return False
        streak = self._outlier_streak[listing.listing_id]
        streak.append(listing.price)
        recent = streak[-RESET_STREAK:]
        if len(recent) < RESET_STREAK or max(recent) > min(recent) * RESET_SPREAD:
            return False
        log.warning(
            "price_reference_reset",
            listing_id=listing.listing_id,
            old_reference=str(self.own_reference(listing)[0]),
            new_level=str(statistics.median(recent)),
        )
        self._prices[listing.listing_id] = deque(recent[:-1], maxlen=self._window)
        return True


def validate_batch(listings: Sequence[RawListing], tracker: ReferenceTracker) -> ValidationReport:
    """Validate one fetch (one point in time), updating the tracker as it goes."""
    peers = _prices_by_item(listings)
    report = ValidationReport()
    for listing in listings:
        rejection = check(listing, _reference(listing, tracker, peers))
        if (
            rejection is not None
            and rejection.reason is RejectReason.OUTLIER
            and tracker.outlier_means_new_level(listing)
        ):
            rejection = None
        if rejection is None:
            tracker.observe(listing)
            report.accepted.append(listing)
        else:
            report.rejected.append(rejection)
    return report


def _reference(
    listing: RawListing, tracker: ReferenceTracker, peers: dict[str, list[tuple[str, Decimal]]]
) -> Decimal | None:
    own, n = tracker.own_reference(listing)
    if n >= MIN_OWN_HISTORY:
        return own
    # Median over ALL stores' prices for this item (itself included): with 3+ values one
    # glitch can't move it. (A median of just 2 is their mean, which a 100x price swings.)
    batch = [p for _, p in peers.get(listing.item_id, [])]
    if len(batch) >= MIN_PEERS:
        return statistics.median(batch)
    return own


def _prices_by_item(listings: Sequence[RawListing]) -> dict[str, list[tuple[str, Decimal]]]:
    out: dict[str, list[tuple[str, Decimal]]] = defaultdict(list)
    for listing in listings:
        if listing.price is not None and listing.price > 0:
            out[listing.item_id].append((listing.listing_id, listing.price))
    return out


__all__ = ["ReferenceTracker", "Rejection", "validate_batch"]
