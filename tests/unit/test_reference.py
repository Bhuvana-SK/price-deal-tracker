"""The outlier reference must survive a glitch on day one and recover from bad history."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tests.conftest import make_listing
from tracker.domain import RawListing, RejectReason
from tracker.ingest.reference import ReferenceTracker, validate_batch

T0 = datetime(2026, 7, 15, 16, tzinfo=UTC)


def store(name: str, price: str, hours: int = 0, item: str = "tv") -> RawListing:
    return make_listing(
        source_listing_id=f"{name}:TV",
        item_id=item,
        price=Decimal(price),
        fetched_at=T0 + timedelta(hours=hours),
    )


def test_first_day_spike_is_caught_by_other_stores() -> None:
    """Regression: a 100x first price used to become the reference and poison the listing."""
    tracker = ReferenceTracker({})
    report = validate_batch(
        [
            store("shopmart", "1299.99"),
            store("dealdepot", "128146.99"),
            store("bytebazaar", "1251.99"),
        ],
        tracker,
    )
    assert [r.listing.source_listing_id for r in report.rejected] == ["dealdepot:TV"]
    assert report.rejected[0].reason is RejectReason.OUTLIER
    # ...and the next normal price for that store is accepted
    later = validate_batch(
        [
            store("shopmart", "1301.99", 6),
            store("dealdepot", "1339.99", 6),
            store("bytebazaar", "1249.99", 6),
        ],
        tracker,
    )
    assert later.rejected == []


def test_poisoned_history_self_heals_after_consistent_prices() -> None:
    tracker = ReferenceTracker(
        {"mock:solo:TV": [Decimal("128146.99")]}
    )  # bad reference from the DB
    outcomes = [
        bool(validate_batch([store("solo", p, h, item="solo-item")], tracker).accepted)
        for h, p in enumerate(["1299.99", "1310.99", "1289.99", "1305.99"])
    ]
    assert outcomes == [False, False, True, True]  # 3rd consistent price resets the reference


def test_isolated_spikes_never_reset_the_reference() -> None:
    history = {"mock:solo:TV": [Decimal("1299.99")] * 5}
    tracker = ReferenceTracker(history)
    prices = ["129999.00", "1300.99", "131000.00", "1299.99", "128000.00"]
    accepted = [
        bool(validate_batch([store("solo", p, h, item="solo-item")], tracker).accepted)
        for h, p in enumerate(prices)
    ]
    assert accepted == [False, True, False, True, False]


def test_established_history_beats_peers() -> None:
    """A listing with its own history is judged on it, even if other stores differ a lot."""
    tracker = ReferenceTracker({"mock:premium:TV": [Decimal("5000")] * 5})
    report = validate_batch(
        [store("premium", "4999.99"), store("a", "400.00"), store("b", "410.00")], tracker
    )
    assert report.rejected == []
