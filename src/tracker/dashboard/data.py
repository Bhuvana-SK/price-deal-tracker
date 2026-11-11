"""Read-only queries behind the dashboard. Each returns a pandas DataFrame."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb
import pandas as pd


class DatabaseBusyError(RuntimeError):
    """The pipeline holds the DuckDB write lock right now."""


@contextmanager
def read_only(
    path: Path, attempts: int = 5, wait_s: float = 0.4
) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open DuckDB read-only, retrying briefly while the pipeline is writing."""
    for attempt in range(attempts):
        try:
            conn = duckdb.connect(str(path), read_only=True)
            break
        except duckdb.IOException as exc:
            if attempt == attempts - 1:
                raise DatabaseBusyError(str(exc)) from exc
            time.sleep(wait_s)
    try:
        yield conn
    finally:
        conn.close()


def _df(
    conn: duckdb.DuckDBPyConnection, sql: str, params: list[object] | None = None
) -> pd.DataFrame:
    return conn.execute(sql, params or []).df()


def has_gold_tables(conn: duckdb.DuckDBPyConnection) -> bool:
    """True once `tracker transform` has built the gold schema."""
    row = conn.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'gold' AND table_name = 'mart_listing_current'"
    ).fetchone()
    return bool(row and row[0])


def current_listings(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Latest state of every listing, deals first."""
    return _df(
        conn,
        """
        SELECT item_id, item_name, listing_id, store, title, url, currency,
               current_price, current_shipping, current_total_cost, in_stock, as_of,
               target_price, is_below_target, pct_drop_24h, avg_30d, avg_90d, min_90d,
               all_time_low, pct_vs_avg_30d, is_90d_low, was_inflated_14d,
               has_enough_history, deal_flag, deal_reason
        FROM gold.mart_listing_current
        ORDER BY deal_flag DESC, pct_vs_avg_30d NULLS LAST
        """,
    )


def price_history(conn: duckdb.DuckDBPyConnection, item_id: str, days: int | None) -> pd.DataFrame:
    """Daily close per listing for one item, with that day's deal flag."""
    since = "AND price_date >= current_date - ?::INTEGER" if days else ""
    params: list[object] = [item_id] + ([days] if days else [])
    return _df(
        conn,
        f"""
        SELECT s.price_date, s.listing_id, s.store, l.title, s.currency, s.close_price,
               s.close_total_cost, s.avg_30d, s.deal_flag
        FROM gold.fct_listing_daily_signals AS s
        JOIN app.listings AS l USING (listing_id)
        WHERE s.item_id = ? {since}
        ORDER BY s.listing_id, s.price_date
        """,
        params,
    )


def store_comparison(conn: duckdb.DuckDBPyConnection, item_id: str) -> pd.DataFrame:
    """Current in-stock listings for one item ranked by total cost."""
    return _df(
        conn,
        """
        SELECT cost_rank, store, title, listing_id, currency, current_price, current_shipping,
               current_total_cost, extra_vs_cheapest, deal_flag
        FROM gold.mart_store_comparison WHERE item_id = ? ORDER BY cost_rank, store
        """,
        [item_id],
    )


def rejected_by_week(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Validator rejections per week (all sources)."""
    return _df(
        conn,
        """
        SELECT date_trunc('week', obs_date) AS week, sum(n_loaded) AS loaded,
               sum(n_rejected) AS rejected, sum(n_non_positive) AS zero_or_negative,
               sum(n_outlier) AS outliers
        FROM gold.mart_data_quality_daily GROUP BY 1 ORDER BY 1
        """,
    )


def quality_totals(conn: duckdb.DuckDBPyConnection) -> dict[str, float]:
    """All-time loaded / rejected counts for the stat tiles."""
    row = conn.execute(
        "SELECT coalesce(sum(n_loaded), 0), coalesce(sum(n_rejected), 0), "
        "coalesce(sum(n_non_positive), 0), coalesce(sum(n_outlier), 0) "
        "FROM gold.mart_data_quality_daily"
    ).fetchone()
    loaded, rejected, zero, outlier = (float(v) for v in (row or (0, 0, 0, 0)))
    total = loaded + rejected
    return {
        "loaded": loaded,
        "rejected": rejected,
        "zero_or_negative": zero,
        "outliers": outlier,
        "reject_rate": rejected / total if total else 0.0,
    }


def recent_runs(conn: duckdb.DuckDBPyConnection, limit: int = 20) -> pd.DataFrame:
    """Latest ingest runs."""
    return _df(
        conn,
        """
        SELECT started_at, source, kind, status, n_attempted AS attempted, n_loaded AS loaded,
               n_duplicate AS duplicates, n_rejected AS rejected,
               n_parse_failures AS parse_failures, layout_broken
        FROM bronze.ingest_runs ORDER BY started_at DESC LIMIT ?
        """,
        [limit],
    )


def alerts_log(conn: duckdb.DuckDBPyConnection, limit: int = 50) -> pd.DataFrame:
    """Recently sent or failed alerts."""
    return _df(
        conn,
        """
        SELECT sent_at, status, rule_type, subject_id, price, currency, channels, message
        FROM app.alerts_sent ORDER BY sent_at DESC LIMIT ?
        """,
        [limit],
    )
