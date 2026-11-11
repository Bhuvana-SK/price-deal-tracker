"""Text embeddings for product titles.

`FastEmbedder` runs a small local ONNX model (BAAI/bge-small-en-v1.5, ~64 MB,
downloaded once to `data/models/`). It needs no account. Tests use
`HashingEmbedder`, which is deterministic and needs no download.
"""

import hashlib
import re
from collections.abc import Sequence
from functools import cache
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from tracker.settings import get_settings

Vector = npt.NDArray[np.float32]


class Embedder(Protocol):
    """Maps texts to L2-normalised vectors."""

    name: str

    def embed(self, texts: Sequence[str]) -> Vector: ...


def cosine(a: Vector, b: Vector) -> float:
    """Cosine similarity of two normalised vectors."""
    return float(np.dot(a, b))


class FastEmbedder:
    """Local sentence-embedding model via fastembed."""

    def __init__(self, model_name: str | None = None) -> None:
        settings = get_settings()
        self.name = model_name or settings.embedding_model
        self._cache_dir = str(settings.models_dir)

    def embed(self, texts: Sequence[str]) -> Vector:
        """Embed and normalise."""
        vectors = np.array(
            list(_model(self.name, self._cache_dir).embed(list(texts))), dtype=np.float32
        )
        return _normalise(vectors)


@cache
def _model(name: str, cache_dir: str) -> Any:
    from fastembed import TextEmbedding  # lazy: loading the model takes ~2s

    return TextEmbedding(model_name=name, cache_dir=cache_dir)


class HashingEmbedder:
    """Character-trigram hashing vectors: a no-download stand-in for tests."""

    name = "hashing-trigram-512"

    def __init__(self, dim: int = 512) -> None:
        self._dim = dim

    def embed(self, texts: Sequence[str]) -> Vector:
        """Bag of hashed character trigrams per text."""
        out = np.zeros((len(texts), self._dim), dtype=np.float32)
        for row, text in enumerate(texts):
            padded = f"  {re.sub(r'[^a-z0-9]+', ' ', text.lower())}  "
            for i in range(len(padded) - 2):
                h = int(hashlib.md5(padded[i : i + 3].encode()).hexdigest()[:8], 16)
                out[row, h % self._dim] += 1.0
        return _normalise(out)


def _normalise(v: Vector) -> Vector:
    norms = np.linalg.norm(v, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    result: Vector = v / norms
    return result
