"""Outputs layer: staging directory and destination options."""

from __future__ import annotations

from ..models import OutputsSettings
from ..paths import settings_file
from ._yaml import load_mapping, save_mapping


def load() -> OutputsSettings:
    return OutputsSettings.model_validate(load_mapping(settings_file("outputs")))


def save(settings: OutputsSettings) -> None:
    save_mapping(settings_file("outputs"), settings.model_dump(mode="json", exclude_none=True))
