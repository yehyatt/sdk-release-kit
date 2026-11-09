"""Repos layer: recipe id to absolute checkout path on this machine, plus tool locations."""

from __future__ import annotations

from ..models import ReposSettings
from ..paths import settings_file
from ._yaml import load_mapping, save_mapping


def load() -> ReposSettings:
    return ReposSettings.model_validate(load_mapping(settings_file("repos")))


def save(settings: ReposSettings) -> None:
    save_mapping(settings_file("repos"), settings.model_dump(mode="json", exclude_none=True))
