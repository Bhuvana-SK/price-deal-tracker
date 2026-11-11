"""Decide whether an alert is new information or a repeat."""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from tracker.alerts.models import Alert


@dataclass(frozen=True)
class LastSent:
    """The most recent successfully sent alert for one (subject, rule)."""

    price: Decimal | None
    sent_at: datetime


def should_send(
    alert: Alert,
    last: LastSent | None,
    now: datetime,
    cooldown: timedelta,
    rearm_pct: Decimal,
) -> tuple[bool, str]:
    """Send if never sent, if the price fell further by `rearm_pct`, or the cooldown passed.

    Returns (decision, reason) so suppressed alerts can be logged with a why.
    """
    if last is None:
        return True, "first alert for this rule"
    fell_further = (
        alert.price is not None
        and last.price is not None
        and alert.price <= last.price * (1 - rearm_pct)
    )
    if fell_further:
        return True, f"price fell further ({last.price} -> {alert.price})"
    if now - last.sent_at >= cooldown:
        return True, "cooldown elapsed"
    return False, f"already alerted at {last.price} on {last.sent_at:%Y-%m-%d %H:%M}"


def alert_key(alert: Alert, now: datetime) -> str:
    """Idempotency key: the same alert re-evaluated on the same day maps to one row."""
    raw = "|".join(
        [
            alert.subject_id,
            alert.rule_id,
            alert.listing_id or "",
            str(alert.price),
            f"{now:%Y-%m-%d}",
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:32]
