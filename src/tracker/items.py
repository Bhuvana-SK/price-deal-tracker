"""Load the watchlist from YAML and build item ids."""

import re
from pathlib import Path

import yaml
from pydantic import BaseModel

from tracker.domain import Item


class _ItemsFile(BaseModel):
    items: list[Item]


def load_items_file(path: Path) -> list[Item]:
    """Parse and validate a watchlist YAML file."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {"items": []}
    return _ItemsFile.model_validate(data).items


def slugify(text: str) -> str:
    """'Auralis NC700 Headphones!' -> 'auralis-nc700-headphones'."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not slug:
        raise ValueError(f"cannot build an id from {text!r}")
    return slug
