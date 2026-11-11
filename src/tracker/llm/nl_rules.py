"""Turn "tell me if the Auralis headphones drop below $200" into a validated alert rule.

Claude fills a strict schema (structured outputs); the result is then validated
against the real watchlist (does the item exist? is the threshold sensible?).
Without an API key, a regex parser handles the common phrasings.
"""

import re
from collections.abc import Sequence
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from tracker.alerts.models import Rule, RuleType
from tracker.domain import Item
from tracker.llm.client import LLMUnavailableError, StructuredLLM

SYSTEM = """You convert a shopper's alert request into a structured rule for a price tracker.

Rule types:
- below_price: alert when the price is at or below `threshold` (a price, e.g. 199.99)
- pct_drop_24h: alert when the price falls by at least `threshold` within 24 hours,
  as a FRACTION (15% -> 0.15)
- low_90d: alert when the price is the lowest in 90 days (no threshold)
- genuine_deal: alert when the deal rule says it is a genuine deal (no threshold)

Set item_id to one of the tracked item ids below, or null if the request is about
every item ("anything", "all items"). If the request is ambiguous, unsupported, or
names an item that is not tracked, set understood=false and explain in clarification.
Never invent an item id."""


class RuleDraft(BaseModel):
    """Claude's structured interpretation of a request."""

    understood: bool
    rule_type: Literal["below_price", "pct_drop_24h", "low_90d", "genuine_deal"] | None = None
    item_id: str | None = Field(
        default=None, description="A tracked item id, or null for all items"
    )
    threshold: float | None = None
    clarification: str | None = Field(
        default=None, description="Why the request could not be parsed"
    )


class RuleParseError(ValueError):
    """The request could not be turned into a valid rule; the message says why."""


def parse_rule(text: str, items: Sequence[Item], llm: StructuredLLM | None) -> tuple[Rule, str]:
    """Parse a request into a Rule. Returns (rule, method) where method is 'llm' or 'regex'."""
    if llm is not None:
        try:
            catalog = "\n".join(f"- {i.item_id}: {i.name}" for i in items)
            draft = llm.parse(
                SYSTEM, f"Tracked items:\n{catalog}\n\nRequest: {text}", RuleDraft, 300
            )
            return validate(draft, items, text), "llm"
        except LLMUnavailableError:
            pass  # fall through to the regex parser
    return validate(regex_parse(text, items), items, text), "regex"


def validate(draft: RuleDraft, items: Sequence[Item], text: str) -> Rule:
    """Check a draft against the watchlist and the rule semantics."""
    if not draft.understood or draft.rule_type is None:
        raise RuleParseError(draft.clarification or f"could not understand {text!r}")
    ids = {i.item_id for i in items}
    if draft.item_id is not None and draft.item_id not in ids:
        raise RuleParseError(
            f"unknown item {draft.item_id!r}; tracked items: {', '.join(sorted(ids))}"
        )
    rule_type = RuleType(draft.rule_type)
    threshold = draft.threshold
    if rule_type is RuleType.BELOW_PRICE:
        if threshold is None or threshold <= 0:
            raise RuleParseError("a price alert needs a positive price")
        if draft.item_id is None:
            raise RuleParseError("a price alert needs a specific item")
    elif rule_type is RuleType.DROP_24H:
        if threshold is None:
            raise RuleParseError("a drop alert needs a percentage")
        if threshold >= 1:  # "15" meant 15%
            threshold /= 100
        if not 0 < threshold < 1:
            raise RuleParseError("drop percentage must be between 0 and 100")
    else:
        threshold = None
    value = Decimal(str(round(threshold, 4))) if threshold is not None else None
    rule_id = f"nl:{rule_type.value}:{draft.item_id or 'all'}:{value or ''}"
    return Rule(rule_id=rule_id, rule_type=rule_type, item_id=draft.item_id, threshold=value)


_MONEY = r"\$?\s?(\d+(?:[.,]\d{1,2})?)"
_BELOW = re.compile(rf"\b(?:below|under|less than|cheaper than|drops? to|at most|<)\s*{_MONEY}")
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s?%")
_ALL = re.compile(r"\b(anything|any item|all items|everything|any product)\b")
_STOP = {
    "the",
    "a",
    "an",
    "me",
    "if",
    "tell",
    "when",
    "my",
    "of",
    "for",
    "price",
    "drops",
    "drop",
    "below",
    "under",
    "alert",
    "notify",
    "let",
    "know",
    "is",
    "goes",
    "gets",
    "to",
    "in",
    "on",
}


def regex_parse(text: str, items: Sequence[Item]) -> RuleDraft:
    """Handle the common phrasings without an LLM."""
    t = text.lower()
    item_id = None if _ALL.search(t) else _best_item(t, items)
    if (m := _BELOW.search(t)) and "%" not in t[m.start() : m.end() + 1]:
        return RuleDraft(
            understood=True,
            rule_type="below_price",
            item_id=item_id,
            threshold=float(m.group(1).replace(",", ".")),
        )
    if (m := _PCT.search(t)) and re.search(r"\b(drop|fall|fell|down|cheaper|off)", t):
        return RuleDraft(
            understood=True, rule_type="pct_drop_24h", item_id=item_id, threshold=float(m.group(1))
        )
    if re.search(r"\b(lowest|90[- ]day low|cheapest in (?:3|three) months)\b", t):
        return RuleDraft(understood=True, rule_type="low_90d", item_id=item_id)
    if re.search(r"\b(real|genuine|actual|true) deal\b|\bdeal\b", t):
        return RuleDraft(understood=True, rule_type="genuine_deal", item_id=item_id)
    return RuleDraft(
        understood=False, clarification="no price, percentage, 90-day low or deal request found"
    )


def _best_item(text: str, items: Sequence[Item]) -> str | None:
    """The item whose name shares the most distinctive words with the text."""
    words = set(re.findall(r"[a-z0-9]+", text)) - _STOP
    best, best_score = None, 0
    for item in items:
        name_words = set(re.findall(r"[a-z0-9]+", f"{item.name} {item.item_id}".lower())) - _STOP
        score = len(words & name_words)
        if score > best_score:
            best, best_score = item.item_id, score
    return best
