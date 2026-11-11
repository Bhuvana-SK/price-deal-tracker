"""Cheap, explainable rules: two titles cannot be the same product if a hard spec differs.

Deliberately limited to specs that are easy to read reliably (storage, sizes,
capacities, model years, condition). Everything subtler (accessories, model
names, bundles) is left to embeddings + the LLM.
"""

import re

_PATTERNS: dict[str, re.Pattern[str]] = {
    "storage": re.compile(r"\b(\d+)\s?(gb|tb)\b"),
    "screen/size (inches)": re.compile(
        r"\b(\d{1,3}(?:\.\d)?)\s?(?:\"|''|in\b|inch\b|-inch\b|in\.)"
    ),
    "capacity (quarts)": re.compile(r"\b(\d+(?:\.\d)?)\s?-?\s?(?:qt|quart)s?\b"),
    "model year": re.compile(r"\b(20[12]\d)\b"),
}
_CONDITION = re.compile(r"\b(renewed|refurbished|refurb|used|open[- ]box|pre-owned)\b")


def normalise(title: str) -> str:
    """Lowercase, unify quotes and spacing."""
    t = title.lower().replace("”", '"').replace("“", '"')
    return re.sub(r"\s+", " ", t).strip()


def extract(title: str) -> dict[str, set[str]]:
    """Hard specs found in a title, e.g. {'storage': {'128gb'}}."""
    t = normalise(title)
    found: dict[str, set[str]] = {}
    for name, pattern in _PATTERNS.items():
        values = {
            "".join(m.groups()) if len(m.groups()) > 1 else m.group(1) for m in pattern.finditer(t)
        }
        if values:
            found[name] = values
    if _CONDITION.search(t):
        found["condition"] = {"used/refurbished"}
    return found


def conflict(a: str, b: str) -> str | None:
    """A human-readable reason if the titles disagree on a hard spec, else None."""
    ea, eb = extract(a), extract(b)
    for name in _PATTERNS:
        if name in ea and name in eb and not (ea[name] & eb[name]):
            return f"different {name}: {'/'.join(sorted(ea[name]))} vs {'/'.join(sorted(eb[name]))}"
    if ("condition" in ea) != ("condition" in eb):
        return "one listing is renewed/refurbished/used"
    return None
