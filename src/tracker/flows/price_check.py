"""Prefect flow: each pipeline step is a task with its own retries and logs.

Run once:   python -m tracker.flows.price_check
Schedule:   tracker serve --cron "0 */6 * * *"   (keeps a local worker running)
"""

import os

# Prefect's anonymous telemetry writes to its local SQLite store while the
# ephemeral server starts, which can log "database is locked". Must be set
# before prefect is imported; override in .env if you want it on.
os.environ.setdefault("PREFECT_SERVER_ANALYTICS_ENABLED", "false")

from prefect import flow, task

from tracker import orchestrate
from tracker.alerts.engine import AlertReport
from tracker.alerts.notifiers import build_notifiers
from tracker.llm.listing_matcher import MatchSummary
from tracker.settings import get_settings
from tracker.storage import repo
from tracker.storage.db import connect
from tracker.transform import DbtOutcome


@task(retries=2, retry_delay_seconds=30, task_run_name="ingest-{name}")
def ingest_task(name: str) -> repo.RunRecord:
    """Fetch, validate and load one source."""
    return orchestrate.ingest_source(name, build_notifiers(get_settings()))


@task
def match_task() -> MatchSummary:
    """Exclude search results that are a different product (embeddings + rules + Claude)."""
    return orchestrate.match_listings()


@flow(name="weekly-digest", log_prints=True)
def weekly_digest() -> str:
    """Summarise what moved this week and send it through the alert channels."""
    return orchestrate.weekly_digest(build_notifiers(get_settings())).text


@task(retries=1, retry_delay_seconds=10)
def dbt_task() -> DbtOutcome:
    """Build silver/gold models and run data tests."""
    return orchestrate.transform(build_notifiers(get_settings()))


@task
def alerts_task() -> AlertReport:
    """Evaluate rules and send de-duplicated alerts."""
    return orchestrate.alerts(build_notifiers(get_settings()))


@flow(name="price-check", log_prints=True)
def price_check() -> dict[str, object]:
    """Ingest every active source, transform, then alert (skipped if dbt fails)."""
    with connect() as conn:
        sources = sorted({s for i in repo.list_items(conn) for s in i.sources})
    runs = []
    for name in sources:
        try:
            runs.append(ingest_task(name))
        except Exception as exc:  # after retries; keep going with the other sources
            print(f"source {name} failed after retries: {exc}")
    match_task()
    dbt = dbt_task()
    alerts = alerts_task() if dbt.success else None
    return {
        "sources": {r.source: r.status for r in runs},
        "dbt_success": dbt.success,
        "alerts_sent": len(alerts.sent) if alerts else 0,
    }


if __name__ == "__main__":
    print(price_check())
