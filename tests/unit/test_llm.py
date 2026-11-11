from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest

from tests.fakes import FakeLLM
from tracker.alerts.models import RuleType
from tracker.domain import Item
from tracker.evaluation.matching_eval import CachedLLM, Pair, score, tune_threshold
from tracker.llm import attributes
from tracker.llm.client import ClaudeLLM, LLMUnavailableError, default_llm
from tracker.llm.digest import DigestText, template_digest, ungrounded_numbers, write_digest
from tracker.llm.embeddings import HashingEmbedder
from tracker.llm.matching import MatchVerdict, ProductMatcher, Thresholds
from tracker.llm.nl_rules import RuleDraft, RuleParseError, parse_rule, regex_parse

ITEMS = [
    Item(item_id="auralis-nc700", name="Auralis NC700 headphones", query="q"),
    Item(item_id="kinetix-k8", name="Kinetix K8 phone (128GB)", query="q"),
    Item(item_id="lumora-oled55", name='Lumora 55" OLED TV', query="q"),
]


# ------------------------------------------------------------------ spec rules
@pytest.mark.parametrize(
    ("a", "b", "fragment"),
    [
        ("Kinetix K8 128GB", "Kinetix K8 256 GB", "storage"),
        ('Lumora 55" OLED TV', "Lumora 65-inch OLED TV", "inches"),
        ("Hearthly 6-Quart Air Fryer", "Hearthly 8 Qt Air Fryer", "quarts"),
        ("Lumora OLED TV (2025)", "Lumora OLED TV 2026", "year"),
        ("Auralis NC700", "Auralis NC700 (Renewed)", "renewed"),
    ],
)
def test_spec_conflicts(a: str, b: str, fragment: str) -> None:
    reason = attributes.conflict(a, b)
    assert reason is not None
    assert fragment in reason


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Kinetix K8 128GB Graphite", "Kinetix K8 5G 128 GB Unlocked"),  # same storage, spaced
        ("Kinetix K8 Smartphone", "Kinetix K8 128GB"),  # spec only on one side: not a conflict
        ('Lumora 55" OLED', "Lumora OLED55 55-Inch"),
    ],
)
def test_no_conflict(a: str, b: str) -> None:
    assert attributes.conflict(a, b) is None


# ------------------------------------------------------------------ matcher
def verdict(same: bool) -> Any:
    return lambda _prompt, schema: schema(same_product=same, confidence=0.9, reason="fake")


def matcher(llm: FakeLLM | None, low: float = 0.2, high: float = 1.01) -> ProductMatcher:
    return ProductMatcher(HashingEmbedder(), Thresholds(low=low, high=high, fallback=0.5), llm=llm)


def test_rules_decide_before_anything_else() -> None:
    llm = FakeLLM(verdict(True))
    d = matcher(llm).decide("Kinetix K8 128GB", "Kinetix K8 256GB")
    assert (d.is_match, d.method) == (False, "rules")
    assert llm.calls == []


def test_low_similarity_is_a_confident_no() -> None:
    llm = FakeLLM(verdict(True))
    d = matcher(llm, low=0.6).decide("Brewix Pro Espresso Machine", "Voltra V9 Robot Vacuum")
    assert (d.is_match, d.method) == (False, "embedding")
    assert llm.calls == []


def test_ambiguous_pairs_go_to_the_llm() -> None:
    llm = FakeLLM(verdict(False))
    m = matcher(llm)
    d = m.decide("Voltra V9 Robot Vacuum", "Voltra V9+ Robot Vacuum")
    assert (d.is_match, d.method) == (False, "llm")
    assert m.llm_calls == 1
    assert "Voltra V9+" in llm.calls[0]


def test_llm_outage_falls_back_to_threshold() -> None:
    d = matcher(FakeLLM(fail=True)).decide(
        "Voltra V9 Robot Vacuum", "Voltra V9 Robot Vacuum Cleaner"
    )
    assert d.method == "embedding_fallback"
    assert d.is_match  # very similar titles clear the 0.5 fallback threshold


# ------------------------------------------------------------------ Claude client
def fake_client(**response: Any) -> Any:
    fields = {"stop_reason": "end_turn", "parsed_output": None, "usage": None} | response
    return SimpleNamespace(messages=SimpleNamespace(parse=lambda **_kw: SimpleNamespace(**fields)))


def test_client_returns_parsed_output() -> None:
    out = MatchVerdict(same_product=True, confidence=0.8, reason="same")
    llm = ClaudeLLM(fake_client(parsed_output=out), "claude-haiku-5-5")
    assert llm.parse("s", "p", MatchVerdict) is out


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_client_rejects_unusable_stop_reasons(stop_reason: str) -> None:
    llm = ClaudeLLM(fake_client(stop_reason=stop_reason), "m")
    with pytest.raises(LLMUnavailableError):
        llm.parse("s", "p", MatchVerdict)


def test_client_maps_connection_errors() -> None:
    def boom(**_kw: Any) -> Any:
        raise anthropic.APIConnectionError(
            request=httpx.Request("POST", "https://api.anthropic.com")
        )

    client = SimpleNamespace(messages=SimpleNamespace(parse=boom))
    with pytest.raises(LLMUnavailableError, match="reach"):
        ClaudeLLM(client, "m").parse("s", "p", MatchVerdict)


def test_no_key_means_no_llm() -> None:
    assert default_llm() is None


# ------------------------------------------------------------------ natural-language rules
@pytest.mark.parametrize(
    ("text", "rule_type", "item_id", "threshold"),
    [
        (
            "tell me if the auralis headphones drop below $200",
            RuleType.BELOW_PRICE,
            "auralis-nc700",
            Decimal("200"),
        ),
        (
            "alert me when the kinetix phone is under 499.99",
            RuleType.BELOW_PRICE,
            "kinetix-k8",
            Decimal("499.99"),
        ),
        (
            "let me know if the lumora tv falls 20% in a day",
            RuleType.DROP_24H,
            "lumora-oled55",
            Decimal("0.2"),
        ),
        ("notify me when anything hits its lowest price", RuleType.LOW_90D, None, None),
        ("is there a real deal on the kinetix phone?", RuleType.GENUINE_DEAL, "kinetix-k8", None),
    ],
)
def test_regex_parser(
    text: str, rule_type: RuleType, item_id: str | None, threshold: Decimal | None
) -> None:
    rule, method = parse_rule(text, ITEMS, llm=None)
    assert method == "regex"
    assert (rule.rule_type, rule.item_id, rule.threshold) == (rule_type, item_id, threshold)


def test_regex_parser_gives_up_clearly() -> None:
    with pytest.raises(RuleParseError, match="no price"):
        parse_rule("what's the weather like?", ITEMS, llm=None)


def test_llm_rule_is_validated_against_the_watchlist() -> None:
    invented = FakeLLM(
        lambda _p, s: s(understood=True, rule_type="below_price", item_id="sony-xm5", threshold=200)
    )
    with pytest.raises(RuleParseError, match="unknown item"):
        parse_rule("sony xm5 under 200", ITEMS, invented)


def test_llm_rule_percent_is_normalised() -> None:
    llm = FakeLLM(
        lambda _p, s: s(
            understood=True, rule_type="pct_drop_24h", item_id="kinetix-k8", threshold=15
        )
    )
    rule, method = parse_rule("kinetix drops 15%", ITEMS, llm)
    assert method == "llm"
    assert rule.threshold == Decimal("0.15")


def test_llm_outage_uses_regex() -> None:
    rule, method = parse_rule("auralis below 180", ITEMS, FakeLLM(fail=True))
    assert (method, rule.threshold) == ("regex", Decimal("180"))


def test_price_rule_needs_an_item() -> None:
    with pytest.raises(RuleParseError, match="specific item"):
        parse_rule("tell me if anything drops below $50", ITEMS, llm=None)


def test_unparseable_draft_explains_why() -> None:
    draft = regex_parse("hello", ITEMS)
    assert isinstance(draft, RuleDraft)
    assert not draft.understood


# ------------------------------------------------------------------ digest
FACTS = {
    "period": {"start": "2026-11-04", "end": "2026-11-11"},
    "movers": [
        {
            "item": "Auralis Buds 2",
            "currency": "USD",
            "start": 150.76,
            "end": 126.99,
            "week_low": 124.99,
            "best_store": "bytebazaar",
            "pct_change": -15.8,
        }
    ],
    "deals_now": [],
    "alerts_sent": 2,
    "bad_prices_caught": 11,
}


def test_grounding_accepts_rounded_numbers_and_flags_invented_ones() -> None:
    assert ungrounded_numbers("Buds fell 16% to $126.99; 11 bad prices caught", FACTS) == []
    assert ungrounded_numbers("Buds fell to $99.00", FACTS) == ["99.00"]


def test_grounded_llm_digest_is_used() -> None:
    llm = FakeLLM(
        lambda _p, s: s(
            headline="Auralis Buds 2 dropped 16% this week",
            bullets=["Now USD 126.99 at bytebazaar", "11 bad prices filtered"],
        )
    )
    d = write_digest(FACTS, llm)
    assert d.method == "llm"
    assert "126.99" in d.text


def test_hallucinated_digest_falls_back_to_template() -> None:
    llm = FakeLLM(lambda _p, s: s(headline="Buds now $89", bullets=["Save 40%!"]))
    d = write_digest(FACTS, llm)
    assert d.method == "template"
    assert d.text == template_digest(FACTS)


def test_template_digest_without_data() -> None:
    assert "no price data" in template_digest({"period": None})


def test_digest_schema_limits_bullets() -> None:
    with pytest.raises(ValueError):
        DigestText(headline="h", bullets=[f"b{i}" for i in range(6)])


# ------------------------------------------------------------------ evaluation helpers
def test_threshold_tuning_and_scoring() -> None:
    pairs = [Pair(i, "a", "b", label, "k") for i, label in enumerate([True, True, False, False])]
    values = {0: 0.9, 1: 0.8, 2: 0.7, 3: 0.3}
    t = tune_threshold(pairs, lambda p: values[p.pair_id])
    assert t == 0.8
    s = score("x", pairs, lambda p: values[p.pair_id] >= t)
    assert (s.precision, s.recall, s.f1) == (1.0, 1.0, 1.0)


def test_cached_llm_replays_without_a_key(tmp_path: Path) -> None:
    cache_file = tmp_path / "cache.json"
    live = CachedLLM(FakeLLM(verdict(True)), cache_file, "fake-model")
    first = live.parse("sys", "prompt", MatchVerdict)
    live.save()
    offline = CachedLLM(None, cache_file, "fake-model")
    assert offline.parse("sys", "prompt", MatchVerdict) == first
    with pytest.raises(LLMUnavailableError):
        offline.parse("sys", "other prompt", MatchVerdict)
    assert offline.misses == 1
