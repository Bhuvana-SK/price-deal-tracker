"""Decide whether two listings are the same product.

Pipeline (cheapest first):
1. hard-spec rules: a storage/size/year/condition conflict means "different"
2. embedding similarity: confident above `high`, confident below `low`
3. the ambiguous band in between goes to Claude (structured verdict), or, with
   no API key, falls back to the embedding threshold
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from tracker.llm import attributes
from tracker.llm.client import LLMUnavailableError, StructuredLLM
from tracker.llm.embeddings import Embedder, Vector, cosine
from tracker.log import get_logger

log = get_logger(__name__)
Method = Literal["rules", "embedding", "llm", "embedding_fallback"]
PROMPT_VERSION = "match-v1"

SYSTEM = """You compare two e-commerce listing titles and decide if they are the EXACT same product.

Same product means a shopper would receive exactly the same thing: same brand, model and
generation; same configuration (storage, size, capacity, colour, connectivity, carrier,
gender fit); same condition (new vs renewed/refurbished); same bundle contents.
Accessories, spare parts, cases, bundles/kits, and neighbouring models (e.g. "Pro", "Plus",
"II", "+", a different model number) are NOT the same product.
Differences in wording, word order, abbreviations, capitalisation or missing filler words
do not matter. If a spec is mentioned in only one title, do not treat that as a conflict."""


class MatchVerdict(BaseModel):
    """Claude's structured answer."""

    same_product: bool
    confidence: float = Field(ge=0, le=1)
    differing_attribute: str | None = Field(
        default=None, description="The spec that differs, if any (e.g. 'storage', 'model')"
    )
    reason: str = Field(description="One short sentence")


@dataclass(frozen=True)
class MatchDecision:
    """Outcome for one pair, with how it was decided."""

    is_match: bool
    method: Method
    similarity: float
    confidence: float
    reason: str


@dataclass(frozen=True)
class Thresholds:
    """Similarity band: >= high match, < low no match, in between ambiguous."""

    low: float
    high: float
    fallback: float  # used for the ambiguous band when the LLM is unavailable


class ProductMatcher:
    """Rules → embeddings → LLM for the ambiguous middle."""

    def __init__(
        self,
        embedder: Embedder,
        thresholds: Thresholds,
        llm: StructuredLLM | None = None,
        use_rules: bool = True,
    ) -> None:
        self.embedder = embedder
        self.thresholds = thresholds
        self.llm = llm
        self.use_rules = use_rules
        self.llm_calls = 0
        self._vectors: dict[str, Vector] = {}

    def decide(self, a: str, b: str) -> MatchDecision:
        """Same product or not, with the method and a reason."""
        if self.use_rules and (why := attributes.conflict(a, b)):
            return MatchDecision(False, "rules", self._similarity(a, b), 0.95, why)
        sim = self._similarity(a, b)
        t = self.thresholds
        if sim >= t.high:
            return MatchDecision(True, "embedding", sim, sim, f"similarity {sim:.3f} >= {t.high}")
        if sim < t.low:
            return MatchDecision(
                False, "embedding", sim, 1 - sim, f"similarity {sim:.3f} < {t.low}"
            )
        if self.llm is not None:
            try:
                return self._ask_llm(a, b, sim)
            except LLMUnavailableError as exc:
                log.warning("llm_match_fallback", reason=str(exc))
        is_match = sim >= t.fallback
        return MatchDecision(
            is_match,
            "embedding_fallback",
            sim,
            0.5,
            f"ambiguous ({sim:.3f}); no LLM, threshold {t.fallback}",
        )

    def _similarity(self, a: str, b: str) -> float:
        missing = [t for t in (a, b) if t not in self._vectors]
        if missing:
            vectors = self.embedder.embed([attributes.normalise(t) for t in missing])
            self._vectors.update(zip(missing, vectors, strict=True))
        return cosine(self._vectors[a], self._vectors[b])

    def _ask_llm(self, a: str, b: str, sim: float) -> MatchDecision:
        assert self.llm is not None
        self.llm_calls += 1
        verdict = self.llm.parse(
            SYSTEM, f"Listing A: {a}\nListing B: {b}", MatchVerdict, max_tokens=400
        )
        reason = verdict.reason + (
            f" (differs: {verdict.differing_attribute})" if verdict.differing_attribute else ""
        )
        return MatchDecision(verdict.same_product, "llm", sim, verdict.confidence, reason)
