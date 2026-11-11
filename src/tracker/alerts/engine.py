"""Evaluate rules against gold tables, de-duplicate, send, and log every attempt."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from tracker.alerts import dedupe
from tracker.alerts.models import Alert
from tracker.alerts.notifiers import Notifier
from tracker.alerts.rules import default_rules, evaluate
from tracker.log import get_logger
from tracker.settings import get_settings
from tracker.sources.base import utcnow
from tracker.storage import repo
from tracker.storage.db import Connection

log = get_logger(__name__)


@dataclass
class AlertReport:
    """What happened in one alert pass."""

    matched: int = 0
    sent: list[Alert] = field(default_factory=list)
    suppressed: int = 0
    failed: int = 0


def run_alerts(
    conn: Connection, notifiers: Sequence[Notifier], now: datetime | None = None
) -> AlertReport:
    """Check every rule against current prices and send what is new."""
    now = now or utcnow()
    items = repo.list_items(conn)
    rules = default_rules(items) + repo.list_rules(conn)
    alerts = evaluate(rules, repo.current_listing_states(conn))
    report = AlertReport(matched=len(alerts))
    for alert in alerts:
        _dispatch(conn, alert, notifiers, now, report)
    log.info(
        "alerts_finished",
        matched=report.matched,
        sent=len(report.sent),
        suppressed=report.suppressed,
        failed=report.failed,
    )
    return report


def send_system_alert(
    conn: Connection,
    notifiers: Sequence[Notifier],
    alert: Alert,
    now: datetime | None = None,
    cooldown: timedelta = timedelta(hours=24),
) -> bool:
    """Send an operational alert (layout change, dbt failure, digest), once per cooldown."""
    report = AlertReport(matched=1)
    _dispatch(conn, alert, notifiers, now or utcnow(), report, cooldown=cooldown)
    return bool(report.sent)


def _dispatch(
    conn: Connection,
    alert: Alert,
    notifiers: Sequence[Notifier],
    now: datetime,
    report: AlertReport,
    cooldown: timedelta | None = None,
) -> None:
    settings = get_settings()
    last = repo.last_sent_alert(conn, alert.subject_id, alert.rule_id)
    send, reason = dedupe.should_send(
        alert,
        last,
        now,
        cooldown or timedelta(hours=settings.alert_cooldown_hours),
        settings.alert_rearm_pct,
    )
    if not send:
        report.suppressed += 1
        log.debug("alert_suppressed", subject=alert.subject_id, rule=alert.rule_id, reason=reason)
        return
    delivered, errors = [], []
    for notifier in notifiers:
        try:
            notifier.send(alert)
            delivered.append(notifier.name)
        except Exception as exc:  # one broken channel must not block the others
            errors.append(f"{notifier.name}: {type(exc).__name__}")
            log.error("alert_send_failed", channel=notifier.name, error=type(exc).__name__)
    status = "sent" if delivered else "failed"
    repo.record_alert(
        conn,
        key=dedupe.alert_key(alert, now),
        alert=alert,
        channels=delivered or [n.name for n in notifiers],
        status=status,
        error="; ".join(errors) or None,
        now=now,
    )
    if delivered:
        report.sent.append(alert)
        log.info("alert_sent", subject=alert.subject_id, rule=alert.rule_id, reason=reason)
    else:
        report.failed += 1
