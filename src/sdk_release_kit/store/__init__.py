"""User settings, one module per layer. Files live in ``paths.user_home()``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import KeysSettings, OutputsSettings, ReposSettings
from . import custom, keys, outputs, repos


@dataclass
class Settings:
    repos: ReposSettings
    outputs: OutputsSettings
    keys: KeysSettings
    custom: dict[str, Any]


def load_all() -> Settings:
    return Settings(
        repos=repos.load(),
        outputs=outputs.load(),
        keys=keys.load(),
        custom=custom.load(),
    )


__all__ = ["Settings", "load_all", "custom", "keys", "outputs", "repos"]
