"""Apply product matching to the pipeline: does each listing a search found belong to its item?

Searches over-match ('kinetix phone' also finds the 256GB model). Each new
(item, listing) link is checked against the item's name. Only a *confident*
non-match (spec conflict, low similarity or an LLM "no") is excluded; when the
LLM is unavailable, ambiguous listings are kept rather than silently dropped.
"""

from dataclasses import dataclass

from tracker.llm.client import default_llm
from tracker.llm.embeddings import Embedder, FastEmbedder, HashingEmbedder
from tracker.llm.matching import ProductMatcher, Thresholds
from tracker.log import get_logger
from tracker.settings import get_settings
from tracker.sources.base import utcnow
from tracker.storage.db import Connection, to_db_ts

log = get_logger(__name__)


@dataclass
class MatchSummary:
    """Counts for one matching pass."""

    checked: int = 0
    kept: int = 0
    excluded: int = 0
    llm_calls: int = 0


def build_matcher() -> ProductMatcher:
    """Matcher configured from settings (Claude only if a key is set)."""
    s = get_settings()
    embedder: Embedder = HashingEmbedder() if s.embedding_model == "hashing" else FastEmbedder()
    # Item names are short, so the title-vs-title cutoff (match_low) would reject real
    # listings; here similarity only rules out clearly unrelated products.
    thresholds = Thresholds(low=s.match_item_floor, high=s.match_high, fallback=s.match_fallback)
    return ProductMatcher(embedder, thresholds, llm=default_llm())


def match_new_listings(
    conn: Connection, matcher: ProductMatcher, rescore: bool = False
) -> MatchSummary:
    """Score unscored item/listing links (or all of them with `rescore`)."""
    where = "" if rescore else "WHERE il.matched_by = 'search'"
    rows = conn.execute(
        f"""
        SELECT il.item_id, il.listing_id, i.name, l.title
        FROM app.item_listings il
        JOIN app.items i USING (item_id)
        JOIN app.listings l USING (listing_id)
        {where}
        ORDER BY il.item_id, il.listing_id
        """
    ).fetchall()
    summary = MatchSummary()
    now = to_db_ts(utcnow())
    model = matcher.llm.model if matcher.llm else None
    for item_id, listing_id, item_name, title in rows:
        d = matcher.decide(item_name, title)
        keep = d.is_match or d.method == "embedding_fallback"
        conn.execute(
            "UPDATE app.item_listings SET matched_by = ?, match_confidence = ?, is_match = ?, "
            "match_reason = ? WHERE item_id = ? AND listing_id = ?",
            [d.method, d.confidence, keep, d.reason, item_id, listing_id],
        )
        conn.execute(
            """
            INSERT INTO app.match_decisions (item_id, listing_id, item_name, title, is_match, method,
                similarity, confidence, reason, model, decided_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (item_id, listing_id) DO UPDATE SET is_match = excluded.is_match,
                method = excluded.method, similarity = excluded.similarity,
                confidence = excluded.confidence, reason = excluded.reason,
                model = excluded.model, decided_at = excluded.decided_at
            """,
            [
                item_id,
                listing_id,
                item_name,
                title,
                keep,
                d.method,
                d.similarity,
                d.confidence,
                d.reason,
                model if d.method == "llm" else None,
                now,
            ],
        )
        summary.checked += 1
        summary.kept += keep
        summary.excluded += not keep
        if not keep:
            log.info("listing_excluded", item_id=item_id, listing_id=listing_id, reason=d.reason)
    summary.llm_calls = matcher.llm_calls
    log.info("matching_finished", **summary.__dict__)
    return summary
