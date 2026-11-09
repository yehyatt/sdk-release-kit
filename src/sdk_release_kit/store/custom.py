"""Custom layer: free-form YAML the team or an AI can extend without code changes.

The raw text is stored as written so comments and unknown keys survive a save. Builders read
known keys through ``${custom.some.key}`` substitution in recipes.
"""

from __future__ import annotations

from typing import Any

import yaml

from ..paths import settings_file
from ._yaml import load_mapping, write_text_atomic


def load() -> dict[str, Any]:
    return load_mapping(settings_file("custom"))


def load_raw() -> str:
    path = settings_file("custom")
    return path.read_text(encoding="utf-8") if path.exists() else ""


def save_raw(text: str) -> dict[str, Any]:
    """Validate that ``text`` is a YAML mapping, then store it verbatim."""
    data = yaml.safe_load(text) if text.strip() else {}
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("custom data must be a YAML mapping at the top level")
    write_text_atomic(settings_file("custom"), text if text.endswith("\n") else text + "\n")
    return data
