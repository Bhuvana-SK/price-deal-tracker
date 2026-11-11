"""Weekly digest: a short summary of what moved, written by Claude from computed facts.

The LLM never sees raw tables, only a small JSON of numbers this module
computed. Its output is then *grounding-checked*: every number it writes must
appear in those facts (within rounding), otherwise the template digest is used.
"""

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, Field

from tracker.llm.client import LLMUnavailableError, StructuredLLM
from tracker.log import get_logger
from tracker.storage.db import Connection

log = get_logger(__name__)
MOVE_THRESHOLD = 0.05  # items that moved at least 5% are "movers"

SYSTEM = """You write a short weekly price digest for one shopper.
Use ONLY the facts provided. Every number you write must appear in the facts
(you may round percentages to whole numbers and prices to 2 decimals).
Lead with what matters most to a shopper (genuine deals, big drops), keep each
bullet under 25 words, and do not give purchasing advice beyond the facts."""


class DigestText(BaseModel):
    """Claude's structured digest."""

    headline: str = Field(description="One sentence, under 15 words")
    bullets: list[str] = Field(description="2 to 5 short bullets", min_length=1, max_length=5)


@dataclass
class Digest:
    """Rendered digest plus provenance."""

    text: str
    method: str  # 'llm' or 'template'
    facts: dict[str, Any]


def weekly_facts(conn: Connection, days: int = 7) -> dict[str, Any]:
    """Compute what moved over the last `days` days of data (best total cost per item)."""
    row = conn.execute("SELECT max(price_date) FROM gold.mart_item_price_daily").fetchone()
    end: date | None = row[0] if row else None
    if end is None:
        return {
            "period": None,
            "movers": [],
            "deals_now": [],
            "alerts_sent": 0,
            "bad_prices_caught": 0,
        }
    start = end - timedelta(days=days)
    movers = conn.execute(
        """
        WITH ends AS (
            SELECT d.item_id, i.name, d.currency,
                   arg_min(d.best_total_cost, d.price_date) FILTER (WHERE d.price_date >= ?) AS start_cost,
                   arg_max(d.best_total_cost, d.price_date) AS end_cost,
                   min(d.best_total_cost) FILTER (WHERE d.price_date >= ?) AS week_low,
                   arg_max(d.best_store, d.price_date) AS best_store
            FROM gold.mart_item_price_daily d JOIN app.items i USING (item_id)
            WHERE d.price_date <= ? GROUP BY 1, 2, 3
        )
        SELECT item_id, name, currency, start_cost, end_cost, week_low, best_store,
               round((end_cost - start_cost) / start_cost * 100, 1) AS pct_change
        FROM ends WHERE start_cost IS NOT NULL AND start_cost <> end_cost
        ORDER BY abs((end_cost - start_cost) / start_cost) DESC
        """,
        [start, start, end],
    ).fetchall()
    deals = conn.execute(
        "SELECT item_name, store, currency, current_price, round(pct_vs_avg_30d * 100, 1) "
        "FROM gold.mart_listing_current WHERE deal_flag ORDER BY pct_vs_avg_30d"
    ).fetchall()
    alerts = conn.execute(
        "SELECT count(*) FROM app.alerts_sent WHERE status = 'sent' AND sent_at >= ?", [start]
    ).fetchone()
    rejected = conn.execute(
        "SELECT coalesce(sum(n_rejected), 0) FROM gold.mart_data_quality_daily WHERE obs_date >= ?",
        [start],
    ).fetchone()
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "movers": [
            {
                "item": r[1],
                "currency": r[2],
                "start": float(r[3]),
                "end": float(r[4]),
                "week_low": float(r[5]),
                "best_store": r[6],
                "pct_change": float(r[7]),
            }
            for r in movers
            if abs(float(r[7])) >= MOVE_THRESHOLD * 100
        ][:6],
        "deals_now": [
            {
                "item": r[0],
                "store": r[1],
                "currency": r[2],
                "price": float(r[3]),
                "pct_vs_30d_avg": float(r[4]),
            }
            for r in deals
        ],
        "alerts_sent": int(alerts[0]) if alerts else 0,
        "bad_prices_caught": int(rejected[0]) if rejected else 0,
    }


def write_digest(facts: dict[str, Any], llm: StructuredLLM | None) -> Digest:
    """LLM digest if available and grounded; otherwise the template."""
    if llm is not None and facts.get("period"):
        try:
            out = llm.parse(
                SYSTEM, "Facts (JSON):\n" + json.dumps(facts, indent=1), DigestText, 600
            )
            text = f"{out.headline}\n" + "\n".join(f"• {b}" for b in out.bullets)
            if bad := ungrounded_numbers(text, facts):
                log.warning("digest_not_grounded", numbers=bad)
            else:
                return Digest(text, "llm", facts)
        except LLMUnavailableError as exc:
            log.warning("digest_llm_fallback", reason=str(exc))
    return Digest(template_digest(facts), "template", facts)


def template_digest(facts: dict[str, Any]) -> str:
    """Deterministic digest from the same facts."""
    if not facts.get("period"):
        return "Weekly digest: no price data yet."
    p = facts["period"]
    movers = facts["movers"]
    lines = [f"Weekly digest {p['start']} → {p['end']}: {len(movers)} item(s) moved 5% or more."]
    for d in facts["deals_now"]:
        lines.append(
            f"• Genuine deal: {d['item']} at {d['store']}, {d['currency']} {d['price']:.2f} "
            f"({d['pct_vs_30d_avg']:.0f}% vs 30-day avg)"
        )
    for m in movers:
        arrow = "▼" if m["pct_change"] < 0 else "▲"
        lines.append(
            f"• {arrow} {m['item']}: {m['currency']} {m['start']:.2f} → {m['end']:.2f} "
            f"({m['pct_change']:+.0f}%), cheapest at {m['best_store']}"
        )
    lines.append(
        f"• {facts['alerts_sent']} alert(s) sent; {facts['bad_prices_caught']} bad price(s) filtered out."
    )
    return "\n".join(lines)


_NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")


def ungrounded_numbers(text: str, facts: dict[str, Any]) -> list[str]:
    """Numbers in `text` that don't match any number in `facts` (allowing rounding)."""
    known = [abs(v) for v in _numbers_in(facts)]
    bad = []
    for raw in _NUMBER.findall(text):
        value = abs(float(raw.replace(",", "")))
        if value in (0, 1, 2, 3, 4, 5, 7, 30, 90) or 1900 <= value <= 2100:
            continue  # small counts, window lengths and years are fine
        if not any(abs(value - k) <= max(0.51, k * 0.005) for k in known):
            bad.append(raw)
    return bad


def _numbers_in(obj: Any) -> list[float]:
    if isinstance(obj, bool):
        return []
    if isinstance(obj, int | float):
        return [float(obj)]
    if isinstance(obj, dict):
        return [n for v in obj.values() for n in _numbers_in(v)]
    if isinstance(obj, list):
        return [n for v in obj for n in _numbers_in(v)]
    if isinstance(obj, str):
        return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", obj)]
    return []
