"""Turn current listing state + rules into alerts (pure functions, no I/O)."""

from collections.abc import Iterable, Sequence
from decimal import Decimal

from tracker.alerts.models import Alert, ListingState, Rule, RuleType
from tracker.domain import Item

DEFAULT_DROP_24H = Decimal("0.15")


def default_rules(items: Iterable[Item]) -> list[Rule]:
    """Built-in rules: each item's target price, plus the smart rules for all items."""
    rules = [
        Rule(
            rule_id=f"target:{i.item_id}",
            rule_type=RuleType.BELOW_PRICE,
            item_id=i.item_id,
            threshold=i.target_price,
        )
        for i in items
        if i.target_price is not None
    ]
    rules += [
        Rule(rule_id="default:low_90d", rule_type=RuleType.LOW_90D),
        Rule(rule_id="default:drop_24h", rule_type=RuleType.DROP_24H, threshold=DEFAULT_DROP_24H),
        Rule(rule_id="default:genuine_deal", rule_type=RuleType.GENUINE_DEAL),
    ]
    return rules


def matches(rule: Rule, s: ListingState) -> bool:
    """Does this listing currently satisfy the rule?"""
    if not s.in_stock or (rule.item_id is not None and rule.item_id != s.item_id):
        return False
    if rule.rule_type is RuleType.BELOW_PRICE:
        return rule.threshold is not None and s.current_price <= rule.threshold
    if rule.rule_type is RuleType.DROP_24H:
        threshold = float(rule.threshold if rule.threshold is not None else DEFAULT_DROP_24H)
        return s.pct_drop_24h is not None and s.pct_drop_24h >= threshold
    if rule.rule_type is RuleType.LOW_90D:
        return bool(s.is_90d_low and s.has_enough_history)
    if rule.rule_type is RuleType.GENUINE_DEAL:
        return bool(s.deal_flag)
    raise ValueError(f"unhandled rule type {rule.rule_type}")  # pragma: no cover


def evaluate(rules: Sequence[Rule], states: Sequence[ListingState]) -> list[Alert]:
    """One alert per (item, rule): the cheapest matching listing by total cost."""
    alerts: list[Alert] = []
    for rule in rules:
        best: dict[str, ListingState] = {}
        for s in states:
            if matches(rule, s) and (
                s.item_id not in best or s.current_total_cost < best[s.item_id].current_total_cost
            ):
                best[s.item_id] = s
        alerts += [build_alert(rule, s) for s in best.values()]
    return alerts


def build_alert(rule: Rule, s: ListingState) -> Alert:
    """Human-readable alert for a matching listing."""
    money = f"{s.currency} {s.current_price}"
    headline = {
        RuleType.BELOW_PRICE: f"Target hit: {s.item_name} is {money} (target {rule.threshold})",
        RuleType.DROP_24H: f"Price drop: {s.item_name} fell {_pct(s.pct_drop_24h)} in 24h to {money}",
        RuleType.LOW_90D: f"90-day low: {s.item_name} is {money}",
        RuleType.GENUINE_DEAL: f"Genuine deal: {s.item_name} is {money}",
    }[rule.rule_type]
    context = [f"at {s.store}", f"total with shipping {s.currency} {s.current_total_cost}"]
    if s.avg_30d is not None:
        context.append(f"30-day avg {s.avg_30d:.2f}")
    if s.all_time_low is not None:
        context.append(f"all-time low {s.all_time_low}")
    body = " · ".join(context)
    if s.deal_reason and rule.rule_type is not RuleType.BELOW_PRICE:
        body += f"\nVerdict: {s.deal_reason}"
    return Alert(
        subject_id=s.item_id,
        rule_id=rule.rule_id,
        rule_type=rule.rule_type.value,
        title=headline,
        body=body,
        listing_id=s.listing_id,
        price=s.current_price,
        currency=s.currency,
        url=s.url,
    )


def _pct(value: float | None) -> str:
    return "?" if value is None else f"{value * 100:.0f}%"
