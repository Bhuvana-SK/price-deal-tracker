"""Measure product matching on the labelled pairs (eval/data/product_pairs.csv).

Thresholds are tuned on the dev split (even pair_id) and every number reported
is on the held-out test split (odd pair_id). Claude verdicts are cached in
eval/results/llm_match_cache.json so results can be reproduced without a key.
"""

import csv
import difflib
import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from tracker.llm import attributes
from tracker.llm.client import LLMUnavailableError, StructuredLLM
from tracker.llm.embeddings import Embedder
from tracker.llm.matching import PROMPT_VERSION, ProductMatcher, Thresholds

T = TypeVar("T", bound=BaseModel)
BLOCK_MARGIN = 0.02  # safety margin below the lowest-scoring true match on dev
ALWAYS_VERIFY = 1.01  # no similarity is high enough to skip verification


@dataclass(frozen=True)
class Pair:
    """One labelled pair."""

    pair_id: int
    a: str
    b: str
    label: bool
    kind: str

    @property
    def split(self) -> str:
        """'dev' (even ids) or 'test' (odd ids)."""
        return "dev" if self.pair_id % 2 == 0 else "test"


@dataclass
class Scores:
    """Confusion counts and derived metrics for one method on one split."""

    name: str
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0
    llm_calls: int = 0
    note: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    @property
    def accuracy(self) -> float:
        n = self.tp + self.fp + self.tn + self.fn
        return (self.tp + self.tn) / n if n else 0.0


def load_pairs(path: Path) -> list[Pair]:
    """Read the labelled CSV."""
    with path.open(encoding="utf-8") as f:
        return [
            Pair(int(r["pair_id"]), r["title_a"], r["title_b"], r["label"] == "1", r["kind"])
            for r in csv.DictReader(f)
        ]


def score(name: str, pairs: Sequence[Pair], predict: Callable[[Pair], bool]) -> Scores:
    """Confusion matrix for a predictor."""
    s = Scores(name)
    for p in pairs:
        guess = predict(p)
        if guess and p.label:
            s.tp += 1
        elif guess:
            s.fp += 1
            s.errors.append(f"FP [{p.kind}] {p.a} | {p.b}")
        elif p.label:
            s.fn += 1
            s.errors.append(f"FN [{p.kind}] {p.a} | {p.b}")
        else:
            s.tn += 1
    return s


def tune_threshold(pairs: Sequence[Pair], value: Callable[[Pair], float]) -> float:
    """The threshold (on `value`) with the best F1 on these pairs; ties go to the higher one."""
    values = sorted({round(value(p), 4) for p in pairs})
    best_t, best_f1 = values[0], -1.0
    for t in values:
        f1 = score("", pairs, lambda p, t=t: value(p) >= t).f1  # type: ignore[misc]
        if f1 >= best_f1:
            best_t, best_f1 = t, f1
    return best_t


def recall_preserving_cutoff(pairs: Sequence[Pair], value: Callable[[Pair], float]) -> float:
    """The highest cutoff that still keeps every true match (100% recall on these pairs)."""
    return min(value(p) for p in pairs if p.label)


def fuzzy_ratio(p: Pair) -> float:
    """Classic string baseline: difflib ratio on sorted, normalised tokens."""

    def tokens(s: str) -> str:
        return " ".join(sorted(re.findall(r"[a-z0-9]+", s.lower())))

    return difflib.SequenceMatcher(None, tokens(p.a), tokens(p.b)).ratio()


class CachedLLM:
    """Replays cached verdicts; only calls the real model (if any) on a cache miss."""

    def __init__(self, inner: StructuredLLM | None, cache_file: Path, model: str) -> None:
        self.inner = inner
        self.model = inner.model if inner else model
        self.cache_file = cache_file
        self.cache: dict[str, dict[str, object]] = (
            json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
        )
        self.misses = 0

    def parse(self, system: str, prompt: str, schema: type[T], max_tokens: int = 1024) -> T:
        key = hashlib.sha256(f"{self.model}|{PROMPT_VERSION}|{prompt}".encode()).hexdigest()[:24]
        if key in self.cache:
            return schema.model_validate(self.cache[key])
        if self.inner is None:
            self.misses += 1
            raise LLMUnavailableError("no API key and no cached verdict")
        result = self.inner.parse(system, prompt, schema, max_tokens)
        self.cache[key] = result.model_dump()
        return result

    def save(self) -> None:
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text(
            json.dumps(self.cache, indent=1, sort_keys=True), encoding="utf-8"
        )


@dataclass
class MatchingReport:
    """Everything the README table needs."""

    results: list[Scores]
    thresholds: dict[str, float]
    n_dev: int
    n_test: int
    embedder: str
    llm_model: str | None


def run_matching_eval(
    pairs_file: Path, embedder: Embedder, llm: CachedLLM | None
) -> MatchingReport:
    """Tune on dev, report on test, for four methods of increasing sophistication."""
    pairs = load_pairs(pairs_file)
    dev = [p for p in pairs if p.split == "dev"]
    test = [p for p in pairs if p.split == "test"]
    probe = ProductMatcher(embedder, Thresholds(0, 1, 0.5), llm=None, use_rules=False)

    def sim(p: Pair) -> float:
        return probe._similarity(p.a, p.b)

    t_fuzzy = tune_threshold(dev, fuzzy_ratio)
    t_emb = tune_threshold(dev, sim)
    # For rules + embeddings, tune only on dev pairs the rules leave undecided.
    undecided = [p for p in dev if attributes.conflict(p.a, p.b) is None]
    t_rules = tune_threshold(undecided, sim)

    results = [
        score("Fuzzy string match (baseline)", test, lambda p: fuzzy_ratio(p) >= t_fuzzy),
        score("Embeddings only", test, lambda p: sim(p) >= t_emb),
    ]
    no_llm = ProductMatcher(embedder, Thresholds(t_rules, t_rules, t_rules), llm=None)
    results.append(
        score("Embeddings + spec rules", test, lambda p: no_llm.decide(p.a, p.b).is_match)
    )

    # Embeddings can't confirm a match here (hard negatives score up to ~0.99), so they
    # are only used to *rule out* pairs: everything above a recall-preserving cutoff
    # goes to the LLM for verification.
    t_block = recall_preserving_cutoff(undecided, sim) - BLOCK_MARGIN
    band = Thresholds(low=t_block, high=ALWAYS_VERIFY, fallback=t_rules)
    thresholds = {"fuzzy": t_fuzzy, "embeddings": t_emb, "rules": t_rules, "band_low": band.low}
    if llm is not None:
        full = ProductMatcher(embedder, band, llm=llm)
        s = score(
            "Embeddings + rules + Claude (ambiguous only)",
            test,
            lambda p: full.decide(p.a, p.b).is_match,
        )
        s.llm_calls = full.llm_calls
        if llm.misses:
            s.note = f"{llm.misses} ambiguous pairs had no cached verdict and used the fallback"
        results.append(s)
        llm.save() if llm.inner is not None else None
    return MatchingReport(
        results, thresholds, len(dev), len(test), embedder.name, llm.model if llm else None
    )


def to_markdown(r: MatchingReport) -> str:
    """Results table + notes for the README."""
    lines = [
        f"Product matching on the held-out **test split** ({r.n_test} pairs; thresholds tuned on "
        f"{r.n_dev} dev pairs). Embedding model: `{r.embedder}`"
        + (f"; LLM: `{r.llm_model}`." if r.llm_model else "."),
        "",
        "| Method | Precision | Recall | F1 | Accuracy | LLM calls |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for s in r.results:
        calls = str(s.llm_calls) if "Claude" in s.name else "n/a"
        lines.append(
            f"| {s.name} | {s.precision:.0%} | {s.recall:.0%} | {s.f1:.2f} | {s.accuracy:.0%} | {calls} |"
        )
    t = r.thresholds
    lines += [
        "",
        f"Thresholds (picked on dev): fuzzy ≥ {t['fuzzy']:.3f}; embeddings ≥ {t['embeddings']:.3f}; "
        f"embeddings after rules ≥ {t['rules']:.3f}. In the Claude pipeline, embeddings only rule "
        f"pairs **out** (similarity < {t['band_low']:.3f}, which keeps every dev match) and Claude "
        "verifies the rest. Hard negatives such as `V9` vs `V9+` score up to 0.99, so no similarity "
        "is safe to auto-accept.",
    ]
    if not any("Claude" in s.name for s in r.results):
        lines.append(
            "\nThe Claude row needs `ANTHROPIC_API_KEY` (or cached verdicts); run "
            "`tracker eval-matching --live` once a key is set."
        )
    notes = [s.note for s in r.results if s.note]
    lines += [f"\nNote: {n}" for n in notes]
    best_no_llm = max((s for s in r.results if "Claude" not in s.name), key=lambda s: s.f1)
    if best_no_llm.errors:
        lines += ["", f'<details><summary>Errors made by "{best_no_llm.name}"</summary>', ""]
        lines += [f"- {e}" for e in best_no_llm.errors]
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"
