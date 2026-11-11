"""Product matching inside the pipeline: wrong variants are excluded before dbt."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from tests.fakes import FakeLLM
from tracker.domain import Item
from tracker.ingest.pipeline import run_ingest
from tracker.llm.embeddings import FastEmbedder, HashingEmbedder
from tracker.llm.listing_matcher import match_new_listings
from tracker.llm.matching import ProductMatcher, Thresholds
from tracker.sources.mock import MockSource, hourly
from tracker.storage import repo
from tracker.storage.db import Connection, connect
from tracker.transform import run_dbt

pytestmark = pytest.mark.integration
END = datetime(2026, 9, 1, tzinfo=UTC)
PHONE = Item(item_id="kinetix-k8", name="Kinetix K8 phone (128GB)", query="kinetix phone")


@pytest.fixture
def db(tmp_path: Path) -> Iterator[tuple[Path, Connection]]:
    path = tmp_path / "m.duckdb"
    with connect(path) as conn:
        repo.upsert_item(conn, PHONE, END)
        run_ingest(
            conn, MockSource(), [PHONE], hourly(END - timedelta(days=20), END, 12), "backfill"
        )
        yield path, conn


def links(conn: Connection) -> list[tuple[object, ...]]:
    return conn.execute(
        "SELECT listing_id, is_match, matched_by FROM app.item_listings ORDER BY listing_id"
    ).fetchall()


def test_other_storage_size_is_excluded_by_rules(db: tuple[Path, Connection]) -> None:
    _, conn = db
    matcher = ProductMatcher(HashingEmbedder(), Thresholds(0.2, 1.01, 0.3), llm=None)
    summary = match_new_listings(conn, matcher)
    assert (summary.checked, summary.excluded) == (6, 3)
    excluded = {lid for lid, keep, _ in links(conn) if not keep}
    assert excluded == {f"mock:{s}:KIN-K8-256" for s in ("shopmart", "bytebazaar", "dealdepot")}
    reasons = conn.execute(
        "SELECT DISTINCT reason FROM app.match_decisions WHERE NOT is_match"
    ).fetchall()
    assert reasons == [("different storage: 128gb vs 256gb",)]


def test_only_new_links_are_scored(db: tuple[Path, Connection]) -> None:
    _, conn = db
    llm = FakeLLM(lambda _p, s: s(same_product=True, confidence=0.9, reason="same"))
    matcher = ProductMatcher(HashingEmbedder(), Thresholds(0.2, 1.01, 0.3), llm=llm)
    match_new_listings(conn, matcher)
    calls = len(llm.calls)
    assert calls == 3  # the 128GB listings; the 256GB ones were settled by rules
    assert match_new_listings(conn, matcher).checked == 0
    assert len(llm.calls) == calls


def test_excluded_listings_do_not_reach_gold(db: tuple[Path, Connection]) -> None:
    path, conn = db
    match_new_listings(conn, ProductMatcher(HashingEmbedder(), Thresholds(0.2, 1.01, 0.3)))
    conn.close()
    assert run_dbt("build", db_path=path).success
    with duckdb.connect(str(path)) as c:
        rows = c.execute("SELECT DISTINCT listing_id FROM gold.mart_listing_current").fetchall()
    assert all("KIN-K8-128" in r[0] for r in rows)
    assert len(rows) == 3


def test_real_embedding_model_ranks_reworded_titles_above_unrelated() -> None:
    """Uses the local fastembed model (downloaded once to data/models)."""
    emb = FastEmbedder("BAAI/bge-small-en-v1.5")
    a, b, c = emb.embed(
        [
            "Kinetix K8 Smartphone 128GB - Graphite",
            "KINETIX K8 (128GB, Graphite) Unlocked",
            "Brewix Pro Espresso Machine with Milk Frother",
        ]
    )
    assert float(a @ b) > 0.85 > float(a @ c)
