from datetime import date, timedelta

from tracker.evaluation.deal_backtest import Day, score
from tracker.sources.mock import PriceEvent

D0 = date(2026, 1, 1)
SALE = PriceEvent("genuine_deal", 10, 13, 0.8)  # 3 days
FAKE = PriceEvent("fake_deal", 30, 33, 0.97)


def label(_listing: str, d: date) -> PriceEvent | None:
    offset = (d - D0).days
    for e in (SALE, FAKE):
        if e.start_day <= offset < e.end_day:
            return e
    return None


def days(flagged_offsets: set[int]) -> list[Day]:
    return [Day("L1", D0 + timedelta(days=i), {"s": i in flagged_offsets}) for i in range(60)]


def test_perfect_strategy() -> None:
    r = score(days({10, 11, 12}), label, "s")
    assert (r.precision, r.sales_caught, r.sales_total, r.fakes_flagged) == (1.0, 1, 1, 0)
    assert r.false_alarms_per_listing_month == 0


def test_one_day_of_a_sale_counts_as_caught() -> None:
    r = score(days({11}), label, "s")
    assert r.sales_caught == 1
    assert r.sale_recall == 1.0


def test_fake_deal_and_false_alarms_are_counted() -> None:
    r = score(days({10, 31, 50}), label, "s")
    assert r.fakes_flagged == 1
    assert r.precision == 1 / 3
    assert r.false_alarms_per_listing_month == 2 / 2  # 2 bad days over 60 days = 2 months


def test_nothing_flagged() -> None:
    r = score(days(set()), label, "s")
    assert (r.flagged_days, r.precision, r.sales_caught) == (0, 0.0, 0)
