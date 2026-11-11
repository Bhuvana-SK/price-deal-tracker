from datetime import date, timedelta
from itertools import pairwise

import pandas as pd
import pytest

from tracker.dashboard.charts import (
    dodge,
    price_history_chart,
    series_labels,
    store_comparison_chart,
)
from tracker.dashboard.palette import DARK, LIGHT, palette_for


def test_dodge_spreads_close_labels_and_keeps_order() -> None:
    out = dodge([133.99, 132.99, 126.99], min_gap=4.0)
    assert out[2] < out[1] < out[0]  # same vertical order as the values
    assert all(b - a >= 4.0 - 1e-9 for a, b in pairwise(sorted(out)))
    assert sum(out) == pytest.approx(133.99 + 132.99 + 126.99)  # centred on the group


def test_dodge_leaves_well_separated_labels_alone() -> None:
    assert dodge([10.0, 50.0, 90.0], min_gap=5.0) == [10.0, 50.0, 90.0]


def test_series_labels_add_product_only_when_a_store_has_several() -> None:
    listings = pd.DataFrame(
        {
            "listing_id": ["a", "b", "c"],
            "store": ["shopmart", "shopmart", "dealdepot"],
            "title": ["Kinetix K8 128GB", "Kinetix K8 256GB", "Kinetix K8 128GB"],
        }
    )
    labels = series_labels(listings)
    assert labels["c"] == "dealdepot"
    assert labels["a"].startswith("shopmart · ")
    assert labels["a"] != labels["b"]


def test_palette_selection_and_fixed_order() -> None:
    assert palette_for("dark") is DARK
    assert palette_for(None) is LIGHT
    assert LIGHT.series_colors(3) == list(LIGHT.series[:3])
    assert LIGHT.series_colors(10)[-1] == LIGHT.muted  # 9th+ series folds to muted, never cycles


def _history() -> pd.DataFrame:
    rows = []
    for i in range(20):
        for lid, store, base in (("a", "shopmart", 100), ("b", "dealdepot", 105)):
            rows.append(
                {
                    "price_date": date(2026, 1, 1) + timedelta(days=i),
                    "listing_id": lid,
                    "store": store,
                    "title": "T",
                    "currency": "USD",
                    "close_price": base - (15 if i in (10, 11) and lid == "a" else 0),
                    "close_total_cost": base,
                    "avg_30d": base,
                    "deal_flag": i in (10, 11) and lid == "a",
                }
            )
    return pd.DataFrame(rows)


def test_price_history_chart_builds_valid_spec() -> None:
    labels = {"a": "shopmart", "b": "dealdepot"}
    spec = price_history_chart(_history(), ["a", "b"], labels, LIGHT, target=95.0).to_dict()
    assert spec["config"]["background"] == LIGHT.surface
    marks = [layer["mark"]["type"] for layer in spec["layer"]]
    assert marks.count("line") == 1
    assert "rule" in marks  # crosshair and target line
    assert "text" in marks  # direct end labels for <= 4 series


def test_store_comparison_chart_builds() -> None:
    df = pd.DataFrame(
        {
            "cost_rank": [1, 2],
            "store": ["bytebazaar", "dealdepot"],
            "title": ["X", "X"],
            "listing_id": ["a", "b"],
            "currency": ["USD", "USD"],
            "current_price": [126.99, 132.99],
            "current_shipping": [0.0, 4.99],
            "current_total_cost": [126.99, 137.98],
            "extra_vs_cheapest": [0.0, 10.99],
            "deal_flag": [False, False],
        }
    )
    spec = store_comparison_chart(df, DARK).to_dict()
    assert spec["config"]["background"] == DARK.surface
