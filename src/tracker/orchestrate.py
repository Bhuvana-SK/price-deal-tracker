"""The end-to-end pipeline as plain functions: ingest → dbt build → alerts.

Prefect (`tracker.flows`) and GitHub Actions both call these, so the logic is
testable without an orchestrator.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta

from tracker.alerts.engine import AlertReport, run_alerts, send_system_alert
from tracker.alerts.models import Alert
from tracker.alerts.notifiers import Notifier, build_notifiers
from tracker.ingest.pipeline import run_ingest
from tracker.llm.client import default_llm
from tracker.llm.digest import Digest, weekly_facts, write_digest
from tracker.llm.listing_matcher import MatchSummary, build_matcher, match_new_listings
from tracker.log import get_logger
from tracker.settings import get_settings
from tracker.sources import get_source
from tracker.storage import repo
from tracker.storage.db import connect
from tracker.transform import DbtOutcome, run_dbt

log = get_logger(__name__)


@dataclass
class PipelineReport:
    """Everything one pipeline run did."""

    runs: list[repo.RunRecord] = field(default_factory=list)
    matching: MatchSummary | None = None
    dbt: DbtOutcome | None = None
    alerts: AlertReport | None = None

    @property
    def ok(self) -> bool:
        """True when dbt passed and no source failed outright."""
        return bool(self.dbt and self.dbt.success) and all(r.status != "failed" for r in self.runs)


def ingest_source(name: str, notifiers: Sequence[Notifier]) -> repo.RunRecord:
    """Ingest one source for all its items; alert if its layout looks broken."""
    with connect() as conn:
        items = [i for i in repo.list_items(conn) if name in i.sources]
        run = run_ingest(conn, get_source(name), items)
        if run.layout_broken:
            send_system_alert(conn, notifiers, _layout_alert(run))
    return run


def match_listings() -> MatchSummary:
    """Check new search results against their item (excludes e.g. other storage sizes)."""
    with connect() as conn:
        return match_new_listings(conn, build_matcher())


def transform(notifiers: Sequence[Notifier]) -> DbtOutcome:
    """Build dbt models; alert (once a day) if models or data tests fail."""
    outcome = run_dbt("build")
    if not outcome.success:
        with connect() as conn:
            send_system_alert(conn, notifiers, _dbt_alert(outcome))
    return outcome


def alerts(notifiers: Sequence[Notifier]) -> AlertReport:
    """Evaluate price rules and send new alerts."""
    with connect() as conn:
        return run_alerts(conn, notifiers)


def run_pipeline(
    sources: Sequence[str] | None = None, notifiers: Sequence[Notifier] | None = None
) -> PipelineReport:
    """One scheduled check. Price alerts are skipped if dbt tests fail (stale/bad data)."""
    notifiers = list(notifiers) if notifiers is not None else build_notifiers(get_settings())
    with connect() as conn:
        active = sorted({s for i in repo.list_items(conn) for s in i.sources})
    report = PipelineReport()
    for name in sources or active:
        try:
            report.runs.append(ingest_source(name, notifiers))
        except Exception as exc:  # a crashing source must not stop the others
            log.error("source_crashed", source=name, error=str(exc))
    report.matching = match_listings()
    report.dbt = transform(notifiers)
    if report.dbt.success:
        report.alerts = alerts(notifiers)
    else:
        log.warning("price_alerts_skipped", reason="dbt build failed")
    return report


def weekly_digest(notifiers: Sequence[Notifier] | None, send: bool = True) -> Digest:
    """Build the weekly digest and (optionally) send it, at most once every 6 days."""
    with connect() as conn:
        digest = write_digest(weekly_facts(conn), default_llm())
        if send and notifiers:
            title, _, body = digest.text.partition("\n")
            alert = Alert(
                subject_id="digest:weekly",
                rule_id="system:weekly_digest",
                rule_type="digest",
                title=f"🗞️ {title}",
                body=body,
            )
            send_system_alert(conn, notifiers, alert, cooldown=timedelta(days=6))
    return digest


def _layout_alert(run: repo.RunRecord) -> Alert:
    return Alert(
        subject_id=f"source:{run.source}",
        rule_id="system:layout_changed",
        rule_type="system",
        title=f"⚠️ Source layout changed: {run.source}",
        body=(
            f"{run.parse_failures} of {run.attempted} records could not be parsed in run "
            f"{run.run_id[:8]}. The parser probably needs updating."
        ),
    )


def _dbt_alert(outcome: DbtOutcome) -> Alert:
    failed = ", ".join(outcome.failures[:5]) or "see logs"
    return Alert(
        subject_id="pipeline:dbt",
        rule_id="system:dbt_failed",
        rule_type="system",
        title="⚠️ dbt build failed: price alerts paused",
        body=f"Failing models/tests: {failed}",
    )
