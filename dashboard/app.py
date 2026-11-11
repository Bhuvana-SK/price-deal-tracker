"""Price & Deal Tracker dashboard.  Run: `make dashboard` (streamlit run dashboard/app.py)."""

from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from tracker.dashboard import charts, data
from tracker.dashboard.palette import palette_for
from tracker.settings import get_settings

st.set_page_config(page_title="Price & Deal Tracker", page_icon="🏷️", layout="wide")
DB = get_settings().db_path
RANGES = {"30 days": 30, "90 days": 90, "180 days": 180, "All": None}


@st.cache_data(ttl=60, show_spinner=False)
def load(name: str, *args: object) -> pd.DataFrame:
    """Run one query from tracker.dashboard.data (cached for a minute)."""
    with data.read_only(DB) as conn:
        result: pd.DataFrame = getattr(data, name)(conn, *args)
        return result


@st.cache_data(ttl=60, show_spinner=False)
def totals() -> dict[str, float]:
    with data.read_only(DB) as conn:
        return data.quality_totals(conn)


def verdict(row: pd.Series) -> str:
    """Status text always pairs an icon with words (never colour alone)."""
    if not row["in_stock"]:
        return "⛔ Out of stock"
    if row["deal_flag"]:
        return "✅ Genuine deal"
    if row["was_inflated_14d"] and (row["pct_vs_avg_30d"] or 0) <= -0.10:
        return "⚠️ Fake discount: price was raised recently"
    if not row["has_enough_history"]:
        return "… Not enough history yet"
    if (row["pct_vs_avg_30d"] or 0) <= -0.10:
        return "🔸 Discounted, but not a 90-day low"
    return "— Not a deal"


def money(v: object) -> str:
    return "–" if v is None or pd.isna(v) else f"{float(v):,.2f}"  # type: ignore[arg-type]


# ----------------------------------------------------------------------------- guard rails
if not DB.exists():
    st.title("🏷️ Price & Deal Tracker")
    st.info(
        "No database yet. Run `make demo` (or `tracker seed && tracker backfill && tracker transform`)."
    )
    st.stop()
try:
    with data.read_only(DB) as _conn:
        ready = data.has_gold_tables(_conn)
except data.DatabaseBusyError:
    st.warning("The pipeline is writing to the database right now. Refresh in a few seconds.")
    st.stop()
if not ready:
    st.info("Raw data is loaded but dbt has not run yet. Run `tracker transform`.")
    st.stop()

palette = palette_for(getattr(getattr(st.context, "theme", None), "type", None))
current = load("current_listings")
q = totals()

# ----------------------------------------------------------------------------- header
st.title("🏷️ Price & Deal Tracker")
as_of = pd.to_datetime(current["as_of"]).max()
age_h = (datetime.now(UTC).replace(tzinfo=None) - as_of.to_pydatetime()).total_seconds() / 3600
st.caption(
    f"Latest prices as of **{as_of:%Y-%m-%d %H:%M} UTC** · {current['item_id'].nunique()} items · "
    f"{current['listing_id'].nunique()} listings · sources: {', '.join(sorted(current['store'].unique()))}"
)
if age_h > 12:
    st.warning(f"⚠️ Data is {age_h:.0f} hours old. Is the scheduler running?")

deals_now = current[current["deal_flag"].fillna(False).astype(bool)]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Genuine deals now", len(deals_now))
c2.metric("At or below target", int(current["is_below_target"].sum()))
c3.metric(
    "Bad prices caught",
    f"{q['rejected']:,.0f}",
    help="$0 prices and 10x+ spikes rejected by the validator",
)
c4.metric("Reject rate", f"{q['reject_rate']:.2%}", help="Rejected / (loaded + rejected), all time")

tab_deals, tab_history, tab_stores, tab_quality, tab_alerts = st.tabs(
    ["Deals now", "Price history", "Store comparison", "Data quality", "Alerts"]
)

# ----------------------------------------------------------------------------- deals
with tab_deals:
    st.subheader("Is this really a deal?")
    if deals_now.empty:
        fake = current[
            current["was_inflated_14d"].fillna(False) & (current["pct_vs_avg_30d"] <= -0.10)
        ]
        st.info(
            "No genuine deals right now."
            + (
                f" {len(fake)} listing(s) *look* 10%+ off but had their price raised in the last 14 days."
                if len(fake)
                else ""
            )
        )
    else:
        for r in deals_now.itertuples():
            st.success(
                f"✅ **{r.item_name}** at {r.store}: {r.currency} {money(r.current_price)} · "
                f"{abs(r.pct_vs_avg_30d):.0%} below its 30-day average · 90-day low {money(r.min_90d)}"
            )
    table = pd.DataFrame(
        {
            "Verdict": current.apply(verdict, axis=1),
            "Item": current["item_name"],
            "Store": current["store"],
            "Price": current["current_price"].astype(float),
            "Total incl. shipping": current["current_total_cost"].astype(float),
            "vs 30-day avg": current["pct_vs_avg_30d"],
            "30-day avg": current["avg_30d"],
            "90-day low": current["min_90d"].astype(float),
            "All-time low": current["all_time_low"].astype(float),
            "24h change": -current["pct_drop_24h"],
            "Why": current["deal_reason"],
        }
    )
    st.dataframe(
        table,
        hide_index=True,
        use_container_width=True,
        column_config={
            "vs 30-day avg": st.column_config.NumberColumn(format="percent"),
            "24h change": st.column_config.NumberColumn(format="percent"),
            "Price": st.column_config.NumberColumn(format="%.2f"),
            "Total incl. shipping": st.column_config.NumberColumn(format="%.2f"),
            "30-day avg": st.column_config.NumberColumn(format="%.2f"),
            "90-day low": st.column_config.NumberColumn(format="%.2f"),
            "All-time low": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    st.caption(
        "A **genuine deal** is ≥10% below the 30-day average, within 3% of the 90-day low, "
        "with no price hike in the previous 14 days and at least 14 days of history."
    )

# ----------------------------------------------------------------------------- shared filter row
items = current.drop_duplicates("item_id").sort_values("item_name")
names = dict(zip(items["item_id"], items["item_name"], strict=True))
# Open on an item that already has real history (live sources start with one point).
with_history = set(current.loc[current["has_enough_history"].fillna(False).astype(bool), "item_id"])
default_index = next((i for i, item in enumerate(names) if item in with_history), 0)

with tab_history:
    f1, f2 = st.columns([3, 2])
    item_id = f1.selectbox(
        "Item", list(names), index=default_index, format_func=names.get, key="hist_item"
    )
    span = f2.radio("Range", list(RANGES), index=1, horizontal=True, key="hist_range")
    listings = current[current["item_id"] == item_id]
    history = load("price_history", item_id, RANGES[span])
    if history["price_date"].nunique() < 2:
        st.info(
            f"Only {history['price_date'].nunique()} day of history so far. The chart appears "
            "after the scheduler has collected a second day. Live sources can't be backfilled."
        )
    else:
        order = sorted(listings["listing_id"])
        labels = charts.series_labels(listings)
        target = listings["target_price"].dropna()
        st.altair_chart(
            charts.price_history_chart(
                history, order, labels, palette, float(target.iloc[0]) if len(target) else None
            ),
            use_container_width=True,
            theme=None,
        )
        cols = st.columns(len(listings) if len(listings) <= 4 else 4)
        for col, r in zip(
            cols, listings.sort_values("current_total_cost").itertuples(), strict=False
        ):
            delta = None if pd.isna(r.pct_vs_avg_30d) else f"{r.pct_vs_avg_30d:+.0%} vs 30-day avg"
            col.metric(
                labels[r.listing_id],
                f"{r.currency} {money(r.current_price)}",
                delta,
                delta_color="inverse",
            )
        with st.expander("Show data"):
            st.dataframe(history, hide_index=True, use_container_width=True)

# ----------------------------------------------------------------------------- stores
with tab_stores:
    item_id_s = st.selectbox(
        "Item", list(names), index=default_index, format_func=names.get, key="store_item"
    )
    comparison = load("store_comparison", item_id_s)
    if comparison.empty:
        st.info("No in-stock listings for this item right now.")
    else:
        best = comparison.iloc[0]
        st.markdown(
            f"Cheapest right now: **{best['store']}** at **{best['currency']} {money(best['current_total_cost'])}** "
            "including shipping."
        )
        st.altair_chart(
            charts.store_comparison_chart(comparison, palette), use_container_width=True, theme=None
        )
        if (listings_n := comparison["title"].nunique()) > comparison["store"].nunique():
            st.caption(
                f"ℹ️ This item matches {listings_n} different product titles. Some may be a different "
                "variant (e.g. storage size); product matching (Stage 6) will separate them."
            )
        with st.expander("Show data"):
            st.dataframe(comparison, hide_index=True, use_container_width=True)

# ----------------------------------------------------------------------------- quality
with tab_quality:
    q1, q2, q3, q4 = st.columns(4)
    q1.metric("Rows loaded", f"{q['loaded']:,.0f}")
    q2.metric("Rejected: $0 / negative", f"{q['zero_or_negative']:,.0f}")
    q3.metric("Rejected: 10x+ outliers", f"{q['outliers']:,.0f}")
    q4.metric("Reject rate", f"{q['reject_rate']:.2%}")
    weekly = load("rejected_by_week")
    if not weekly.empty:
        st.markdown("**Bad prices caught per week**")
        st.altair_chart(
            charts.rejected_weekly_chart(weekly, palette), use_container_width=True, theme=None
        )
    st.markdown("**Recent ingest runs**")
    runs = load("recent_runs")
    runs["status"] = runs["status"].map(
        {
            "success": "✅ success",
            "partial": "⚠️ partial",
            "failed": "❌ failed",
            "running": "… running",
        }
    )
    runs["layout_broken"] = runs["layout_broken"].map({True: "⚠️ yes", False: "no"})
    st.dataframe(runs, hide_index=True, use_container_width=True)

# ----------------------------------------------------------------------------- alerts
with tab_alerts:
    log = load("alerts_log")
    if log.empty:
        st.info("No alerts sent yet. Run `tracker run`.")
    else:
        log["status"] = log["status"].map({"sent": "✅ sent", "failed": "❌ failed"})
        st.dataframe(log, hide_index=True, use_container_width=True)
