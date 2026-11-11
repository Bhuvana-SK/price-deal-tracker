"""Altair charts for the dashboard.

Conventions: 2px lines, >=8px markers with a 2px surface ring, hairline grid,
one y-axis, a legend for 2+ series plus direct end labels for <=4, a crosshair
tooltip on line charts, and text in ink colours (never the series colour).
"""

from typing import Any

import altair as alt
import pandas as pd

from tracker.dashboard.palette import Palette

DEAL_MARKER = "Genuine deal (rule fired)"


def series_labels(listings: pd.DataFrame) -> dict[str, str]:
    """listing_id -> legend label. Store name, plus the product when a store has several."""
    per_store = listings.groupby("store")["listing_id"].nunique()
    labels = {}
    for row in listings.drop_duplicates("listing_id").itertuples():
        label = str(row.store)
        if per_store[row.store] > 1:
            label += f" · {_short(str(row.title))}"
        labels[str(row.listing_id)] = label
    return labels


def _short(title: str, n: int = 26) -> str:
    return title if len(title) <= n else title[: n - 1].rstrip() + "…"


def _style(chart: alt.TopLevelMixin, p: Palette) -> Any:
    """Recessive chrome: hairline grid, muted axes, no frame."""
    font = "system-ui, -apple-system, Segoe UI, sans-serif"
    return (
        chart.configure(background=p.surface, font=font)
        .configure_view(strokeWidth=0)
        .configure_axis(
            gridColor=p.grid,
            gridWidth=1,
            domainColor=p.baseline,
            tickColor=p.baseline,
            labelColor=p.muted,
            titleColor=p.text_secondary,
            labelFontSize=11,
            titleFontSize=11,
            titleFontWeight="normal",
        )
        .configure_legend(
            labelColor=p.text_secondary,
            titleColor=p.text_secondary,
            labelFontSize=12,
            symbolStrokeWidth=2,
        )
    )


def price_history_chart(
    history: pd.DataFrame,
    order: list[str],
    labels: dict[str, str],
    p: Palette,
    target: float | None,
) -> Any:
    """Daily close per listing, deal days marked, target line, crosshair tooltip.

    `order` is the item's full listing list (not just what is in range), so each
    listing keeps its colour when the date range changes.
    """
    df = history.copy()
    df["series"] = df["listing_id"].map(labels)
    df["close_price"] = df["close_price"].astype(float)
    domain = [labels[lid] for lid in order]
    color = alt.Color(
        "series:N",
        scale=alt.Scale(domain=domain, range=p.series_colors(len(domain))),
        legend=alt.Legend(title=None, orient="top", direction="horizontal")
        if len(domain) > 1
        else None,
    )
    currency = str(df["currency"].iloc[0]) if len(df) else ""
    x = alt.X("price_date:T", title=None, axis=alt.Axis(format="%b %d", labelAngle=0, tickCount=6))
    y = alt.Y(
        "close_price:Q", title=f"Daily closing price ({currency})", scale=alt.Scale(zero=False)
    )
    base = alt.Chart(df).encode(x=x, y=y)
    lines = base.mark_line(strokeWidth=2, strokeCap="round", strokeJoin="round").encode(color=color)

    deals = (
        base.transform_filter(alt.datum.deal_flag)
        .transform_calculate(marker=f"'{DEAL_MARKER}'")
        .mark_point(filled=True, size=80, opacity=1, color=p.good, stroke=p.surface, strokeWidth=2)
        .encode(
            shape=alt.Shape(
                "marker:N",
                scale=alt.Scale(domain=[DEAL_MARKER], range=["circle"]),
                legend=alt.Legend(title=None, orient="top"),
            ),
            tooltip=[
                alt.Tooltip("series:N", title="Listing"),
                alt.Tooltip("price_date:T", title="Date", format="%b %d, %Y"),
                alt.Tooltip("close_price:Q", title="Price", format=",.2f"),
            ],
        )
    )

    has_deals = bool(df["deal_flag"].fillna(False).astype(bool).any())
    layers: list[Any] = [lines, *([deals] if has_deals else []), *_crosshair(df, domain, p)]
    if target is not None:
        layers += _target_line(target, p)
    if 1 < len(domain) <= 4:
        layers.append(_end_labels(df, p))
    return _style(alt.layer(*layers).properties(height=360), p)


def _crosshair(df: pd.DataFrame, domain: list[str], p: Palette) -> list[Any]:
    """Vertical rule + hover dots + one tooltip listing every series on that date."""
    wide = df.pivot_table(
        index="price_date", columns="series", values="close_price", aggfunc="last"
    )
    keys = {s: f"s{i}" for i, s in enumerate(domain)}
    wide = wide.rename(columns=keys).reset_index()
    hover = alt.selection_point(
        fields=["price_date"], nearest=True, on="pointerover", empty=False, clear="pointerout"
    )
    tooltip = [alt.Tooltip("price_date:T", title="Date", format="%b %d, %Y")] + [
        alt.Tooltip(f"{key}:Q", title=series, format=",.2f")
        for series, key in keys.items()
        if key in wide.columns
    ]
    rule = (
        alt.Chart(wide)
        .mark_rule(color=p.muted, strokeWidth=1)
        .encode(
            x="price_date:T",
            opacity=alt.condition(hover, alt.value(1), alt.value(0)),
            tooltip=tooltip,
        )
        .add_params(hover)
    )
    dots = (
        alt.Chart(df)
        .mark_point(filled=True, size=64, stroke=p.surface, strokeWidth=2, opacity=1)
        .encode(
            x="price_date:T",
            y="close_price:Q",
            color=alt.Color("series:N", legend=None),
        )
        .transform_filter(hover)
    )
    return [rule, dots]


def _target_line(target: float, p: Palette) -> list[Any]:
    frame = pd.DataFrame({"target": [target], "label": [f"Target {target:,.2f}"]})
    rule = (
        alt.Chart(frame)
        .mark_rule(color=p.text_secondary, strokeDash=[4, 4], strokeWidth=1)
        .encode(y="target:Q")
    )
    text = (
        alt.Chart(frame)
        .mark_text(
            align="left", baseline="bottom", dx=4, dy=-3, color=p.text_secondary, fontSize=11
        )
        .encode(y="target:Q", text="label:N", x=alt.value(0))
    )
    return [rule, text]


def _end_labels(df: pd.DataFrame, p: Palette) -> Any:
    """Direct labels at each line's last point, in secondary ink."""
    last = df.sort_values("price_date").groupby("series", as_index=False).last()
    last["label_y"] = dodge(last["close_price"].tolist(), min_gap=_label_gap(df))
    return (
        alt.Chart(last)
        .mark_text(align="left", dx=8, color=p.text_secondary, fontSize=11)
        .encode(x="price_date:T", y="label_y:Q", text="series:N")
    )


def _label_gap(df: pd.DataFrame) -> float:
    """~5.5% of the visible price range: about one 11px label on a 360px chart."""
    span = float(df["close_price"].max() - df["close_price"].min())
    return max(span, 1.0) * 0.055


def dodge(values: list[float], min_gap: float) -> list[float]:
    """Spread label positions so no two are closer than `min_gap`, keeping their order.

    Labels are pushed apart symmetrically around the group's centre, so they stay
    next to the lines they name.
    """
    order = sorted(range(len(values)), key=lambda i: values[i])
    placed = [values[i] for i in order]
    for i in range(1, len(placed)):
        placed[i] = max(placed[i], placed[i - 1] + min_gap)
    shift = (sum(values) - sum(placed)) / len(placed) if placed else 0.0
    out = [0.0] * len(values)
    for rank, i in enumerate(order):
        out[i] = placed[rank] + shift
    return out


def store_comparison_chart(df: pd.DataFrame, p: Palette) -> Any:
    """Total cost (price + shipping) per listing, cheapest first, labelled at the bar end."""
    data = df.copy()
    for col in ("current_total_cost", "current_price", "current_shipping"):
        data[col] = data[col].astype(float)
    data["label"] = [
        f"{r.cost_rank}. {r.store}"
        + (f" · {_short(str(r.title), 22)}" if (data["store"] == r.store).sum() > 1 else "")
        for r in data.itertuples()
    ]
    data["value_text"] = [
        f"{r.current_total_cost:,.2f}"
        + (f"  (incl. {r.current_shipping:,.2f} shipping)" if r.current_shipping else "")
        for r in data.itertuples()
    ]
    currency = str(data["currency"].iloc[0]) if len(data) else ""
    y = alt.Y(
        "label:N",
        sort=None,
        title=None,
        scale=alt.Scale(paddingInner=0.45, paddingOuter=0.25),  # visible gap between bars
        axis=alt.Axis(labelLimit=260, labelColor=p.text_secondary, ticks=False),
    )
    # Headroom on the right so the end-of-bar label never clips.
    x = alt.X(
        "current_total_cost:Q",
        title=f"Total cost incl. shipping ({currency})",
        axis=alt.Axis(tickCount=6),
        scale=alt.Scale(domain=[0, float(data["current_total_cost"].max()) * 1.3], nice=False),
    )
    bars = (
        alt.Chart(data)
        .mark_bar(color=p.series[0], cornerRadiusEnd=4)
        .encode(
            x=x,
            y=y,
            tooltip=[
                alt.Tooltip("label:N", title="Listing"),
                alt.Tooltip("current_price:Q", title="Price", format=",.2f"),
                alt.Tooltip("current_shipping:Q", title="Shipping", format=",.2f"),
                alt.Tooltip("current_total_cost:Q", title="Total", format=",.2f"),
                alt.Tooltip("extra_vs_cheapest:Q", title="Extra vs cheapest", format=",.2f"),
            ],
        )
    )
    text = (
        alt.Chart(data)
        .mark_text(align="left", dx=6, color=p.text_primary, fontSize=12)
        .encode(x=x, y=y, text="value_text:N")
    )
    height = max(120, 44 * len(data))
    return _style(alt.layer(bars, text).properties(height=height), p)


def rejected_weekly_chart(df: pd.DataFrame, p: Palette) -> Any:
    """Rows rejected by the validator per week (single series: no legend)."""
    data = df.copy()
    data["rejected"] = data["rejected"].astype(float)
    return _style(
        alt.Chart(data)
        .mark_bar(color=p.series[0], cornerRadiusEnd=4, width={"band": 0.7})
        .encode(
            x=alt.X(
                "week:T",
                title=None,
                axis=alt.Axis(format="%b %d", labelAngle=0),
                scale=alt.Scale(padding=16),  # keep the first/last bar inside the plot
            ),
            y=alt.Y("rejected:Q", title="Rows rejected"),
            tooltip=[
                alt.Tooltip("week:T", title="Week of", format="%b %d, %Y"),
                alt.Tooltip("rejected:Q", title="Rejected", format=",.0f"),
                alt.Tooltip("zero_or_negative:Q", title="$0 / negative", format=",.0f"),
                alt.Tooltip("outliers:Q", title="Outliers (≥10x off)", format=",.0f"),
                alt.Tooltip("loaded:Q", title="Loaded", format=",.0f"),
            ],
        )
        .properties(height=220),
        p,
    )
