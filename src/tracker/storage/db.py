"""DuckDB connection helpers.

DuckDB allows one writing process at a time. The pipeline holds a write
connection only for the length of a run; the dashboard opens read-only.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from tracker.settings import get_settings

SCHEMA_SQL = Path(__file__).with_name("schema.sql")
Connection = duckdb.DuckDBPyConnection


@contextmanager
def connect(path: Path | None = None, read_only: bool = False) -> Iterator[Connection]:
    """Open the database (creating it and its schema if needed) and always close it."""
    db_path = path or get_settings().db_path
    if not read_only:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db_path), read_only=read_only)
    try:
        if not read_only:
            init_schema(conn)
        yield conn
    finally:
        conn.close()


def init_schema(conn: Connection) -> None:
    """Create schemas and tables if they do not exist."""
    conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))


@contextmanager
def transaction(conn: Connection) -> Iterator[Connection]:
    """BEGIN/COMMIT, rolling back on any exception."""
    conn.begin()
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def to_db_ts(ts: datetime) -> datetime:
    """Convert an aware datetime to naive UTC for TIMESTAMP columns."""
    if ts.tzinfo is None:
        return ts
    return ts.astimezone(UTC).replace(tzinfo=None)


def bulk_insert(
    conn: Connection,
    table: str,
    columns: Sequence[tuple[str, str, Sequence[Any]]],
    on_conflict: str = "DO NOTHING",
) -> None:
    """Insert many rows at once by passing each column as a typed list.

    `columns` is a sequence of (column name, DuckDB type, values). This is
    ~200x faster than `executemany` because DuckDB unnests the arrays in one
    vectorised statement.
    """
    if not columns or not columns[0][2]:
        return
    names = ", ".join(name for name, _, _ in columns)
    selects = ", ".join(f"unnest(${i + 1}::{typ}[])" for i, (_, typ, _) in enumerate(columns))
    sql = f"INSERT INTO {table} ({names}) SELECT {selects} ON CONFLICT {on_conflict}"
    conn.execute(sql, [list(values) for _, _, values in columns])
