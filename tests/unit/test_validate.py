from decimal import Decimal

import pytest

from tests.conftest import make_listing
from tracker.domain import RejectReason
from tracker.ingest.validate import check, validate_all


@pytest.mark.parametrize(
    ("price", "currency", "reference", "expected"),
    [
        (None, "USD", None, RejectReason.MISSING_PRICE),
        (Decimal("0"), "USD", None, RejectReason.NON_POSITIVE),
        (Decimal("-5"), "USD", None, RejectReason.NON_POSITIVE),
        (Decimal("2000000"), "USD", None, RejectReason.ABSURD),
        (Decimal("10"), "US", None, RejectReason.BAD_CURRENCY),
        (Decimal("24999"), "USD", Decimal("249.99"), RejectReason.OUTLIER),  # 100x spike
        (Decimal("2.49"), "USD", Decimal("249.99"), RejectReason.OUTLIER),  # 1/100 typo
    ],
)
def test_rejects_impossible_prices(
    price: Decimal | None, currency: str, reference: Decimal | None, expected: RejectReason
) -> None:
    rejection = check(make_listing(price=price, currency=currency), reference)
    assert rejection is not None
    assert rejection.reason is expected


@pytest.mark.parametrize(
    ("price", "reference"),
    [
        (Decimal("199.99"), Decimal("249.99")),  # a real 20% sale
        (Decimal("499.99"), Decimal("249.99")),  # 2x is suspicious but possible
        (Decimal("249.99"), None),  # no history yet
    ],
)
def test_accepts_plausible_prices(price: Decimal, reference: Decimal | None) -> None:
    assert check(make_listing(price=price), reference) is None


def test_validate_all_counts_by_reason() -> None:
    listings = [
        make_listing(price=Decimal("100")),
        make_listing(price=Decimal("0")),
        make_listing(price=Decimal("0")),
        make_listing(price=Decimal("10000")),
    ]
    report = validate_all(listings, reference=lambda _l: Decimal("100"))
    assert len(report.accepted) == 1
    assert report.counts() == {"non_positive_price": 2, "price_outlier": 1}
