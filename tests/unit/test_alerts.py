from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx

from tracker.alerts.dedupe import LastSent, alert_key, should_send
from tracker.alerts.models import Alert, ListingState, Rule, RuleType
from tracker.alerts.notifiers import (
    ConsoleNotifier,
    DiscordNotifier,
    TelegramNotifier,
    build_notifiers,
)
from tracker.alerts.rules import default_rules, evaluate, matches
from tracker.domain import Item
from tracker.settings import Settings

NOW = datetime(2026, 6, 1, 12, tzinfo=UTC)


def state(**overrides: Any) -> ListingState:
    fields: dict[str, Any] = {
        "item_id": "hp",
        "item_name": "Headphones",
        "listing_id": "mock:shopmart:HP",
        "store": "shopmart",
        "title": "Headphones",
        "url": "https://shopmart.example/p/hp",
        "currency": "USD",
        "current_price": Decimal("200.00"),
        "current_total_cost": Decimal("200.00"),
        "in_stock": True,
        "as_of": NOW,
        "target_price": Decimal("210"),
        "pct_drop_24h": 0.0,
        "avg_30d": 240.0,
        "min_90d": Decimal("199.00"),
        "all_time_low": Decimal("190.00"),
        "is_90d_low": False,
        "has_enough_history": True,
        "deal_flag": False,
        "deal_reason": "not meaningfully below the 30-day average",
    }
    fields.update(overrides)
    return ListingState(**fields)


TARGET = Rule(rule_id="t", rule_type=RuleType.BELOW_PRICE, item_id="hp", threshold=Decimal("210"))


# ------------------------------------------------------------------ rules
def test_target_rule_picks_cheapest_matching_listing() -> None:
    states = [
        state(listing_id="a", current_price=Decimal("205"), current_total_cost=Decimal("211.99")),
        state(listing_id="b", current_price=Decimal("208"), current_total_cost=Decimal("208")),
        state(listing_id="c", current_price=Decimal("250"), current_total_cost=Decimal("250")),
    ]
    (alert,) = evaluate([TARGET], states)
    assert alert.listing_id == "b"  # cheapest by total cost, not sticker price
    assert "Target hit" in alert.title


def test_out_of_stock_and_other_items_never_match() -> None:
    assert not matches(TARGET, state(in_stock=False))
    assert not matches(TARGET, state(item_id="tv"))


@pytest.mark.parametrize(
    ("rule", "overrides", "expected"),
    [
        (
            Rule(rule_id="d", rule_type=RuleType.DROP_24H, threshold=Decimal("0.15")),
            {"pct_drop_24h": 0.2},
            True,
        ),
        (
            Rule(rule_id="d", rule_type=RuleType.DROP_24H, threshold=Decimal("0.15")),
            {"pct_drop_24h": 0.1},
            False,
        ),
        (Rule(rule_id="d", rule_type=RuleType.DROP_24H), {"pct_drop_24h": None}, False),
        (Rule(rule_id="l", rule_type=RuleType.LOW_90D), {"is_90d_low": True}, True),
        (
            Rule(rule_id="l", rule_type=RuleType.LOW_90D),
            {"is_90d_low": True, "has_enough_history": False},
            False,
        ),
        (Rule(rule_id="g", rule_type=RuleType.GENUINE_DEAL), {"deal_flag": True}, True),
        (Rule(rule_id="g", rule_type=RuleType.GENUINE_DEAL), {"deal_flag": None}, False),
    ],
)
def test_smart_rules(rule: Rule, overrides: dict[str, Any], expected: bool) -> None:
    assert matches(rule, state(**overrides)) is expected


def test_default_rules_include_targets_and_smart_rules() -> None:
    items = [
        Item(item_id="hp", name="HP", query="q", target_price=Decimal("210")),
        Item(item_id="tv", name="TV", query="q"),
    ]
    ids = {r.rule_id for r in default_rules(items)}
    assert ids == {"target:hp", "default:low_90d", "default:drop_24h", "default:genuine_deal"}


# ------------------------------------------------------------------ de-duplication
ALERT = Alert(
    subject_id="hp", rule_id="t", rule_type="below_price", title="t", body="b", price=Decimal("200")
)
WEEK = timedelta(days=7)


@pytest.mark.parametrize(
    ("last", "now", "expected"),
    [
        (None, NOW, True),  # never sent
        (LastSent(Decimal("200"), NOW - timedelta(hours=6)), NOW, False),  # same price, recent
        (LastSent(Decimal("205"), NOW - timedelta(hours=6)), NOW, True),  # fell 2.4% further
        (LastSent(Decimal("201"), NOW - timedelta(hours=6)), NOW, False),  # fell <2%: noise
        (LastSent(Decimal("200"), NOW - timedelta(days=8)), NOW, True),  # cooldown passed
    ],
)
def test_should_send(last: LastSent | None, now: datetime, expected: bool) -> None:
    send, reason = should_send(ALERT, last, now, WEEK, Decimal("0.02"))
    assert send is expected
    assert reason


def test_alert_key_is_stable_within_a_day() -> None:
    assert alert_key(ALERT, NOW) == alert_key(ALERT, NOW + timedelta(hours=3))
    assert alert_key(ALERT, NOW) != alert_key(ALERT, NOW + timedelta(days=1))


# ------------------------------------------------------------------ notifiers
@respx.mock
def test_discord_posts_and_retries() -> None:
    route = respx.post("https://discord.test/hook").mock(
        side_effect=[httpx.Response(502), httpx.Response(204)]
    )
    DiscordNotifier("https://discord.test/hook").send(ALERT)
    assert route.call_count == 2
    assert b"t\\nb" in route.calls.last.request.content


@respx.mock
def test_telegram_sends_chat_id_and_text() -> None:
    route = respx.post("https://api.telegram.org/botTOKEN/sendMessage").respond(
        200, json={"ok": True}
    )
    TelegramNotifier("TOKEN", "123").send(ALERT)
    body = route.calls.last.request.content
    assert b'"chat_id":"123"' in body.replace(b" ", b"")


@respx.mock
def test_discord_failure_raises_after_retries() -> None:
    respx.post("https://discord.test/hook").respond(500)
    with pytest.raises(httpx.HTTPStatusError):
        DiscordNotifier("https://discord.test/hook").send(ALERT)


def test_build_notifiers_falls_back_to_console() -> None:
    (only,) = build_notifiers(Settings(_env_file=None))  # type: ignore[call-arg]
    assert isinstance(only, ConsoleNotifier)


def test_build_notifiers_uses_configured_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/hook")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    names = [n.name for n in build_notifiers(Settings(_env_file=None))]  # type: ignore[call-arg]
    assert names == ["discord", "telegram"]
