"""Load recipes and resolve ``${...}`` variables.

Load order (later deep-merges over earlier):
1. ``recipes/sdks.yaml`` shipped with the tool (portable, in git)
2. ``<user_home>/recipes.d/*.yaml`` in name order (this machine only)
3. the file named by ``SDK_RELEASE_KIT_RECIPES``, if set

Variables usable in recipe strings:
``${custom.a.b}``, ``${env.NAME}``, ``${version}``, ``${recipe}``; add ``:-default`` for a
fallback, e.g. ``${custom.ios_team_id:-}``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from .models import RecipesFile
from .paths import data_dir, recipe_overlay_dir

_VAR = re.compile(r"\$\{([A-Za-z_][\w.\-]*)(?::-([^}]*))?\}")


class RecipeError(ValueError):
    pass


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def recipe_sources() -> list[Path]:
    sources = [data_dir("recipes") / "sdks.yaml"]
    overlay_dir = recipe_overlay_dir()
    if overlay_dir.is_dir():
        sources += sorted(overlay_dir.glob("*.yaml")) + sorted(overlay_dir.glob("*.yml"))
    extra = os.environ.get("SDK_RELEASE_KIT_RECIPES")
    if extra:
        sources.append(Path(extra).expanduser())
    return sources


def load_recipes() -> RecipesFile:
    merged: dict[str, Any] = {}
    for src in recipe_sources():
        data = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise RecipeError(f"{src} must contain a YAML mapping")
        merged = deep_merge(merged, data)
    try:
        return RecipesFile.model_validate(merged)
    except Exception as exc:  # pydantic.ValidationError, re-raised with the source list
        names = ", ".join(str(s) for s in recipe_sources())
        raise RecipeError(f"invalid recipes (from {names}):\n{exc}") from exc


def _lookup(variables: dict[str, Any], dotted: str) -> Any:
    node: Any = variables
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif hasattr(node, "get") and not isinstance(node, dict):
            node = node.get(part)  # os.environ
            if node is None:
                return None
        else:
            return None
    return node


def substitute(value: Any, variables: dict[str, Any]) -> Any:
    """Recursively replace ``${...}`` in strings inside ``value``."""
    if isinstance(value, str):

        def repl(match: re.Match[str]) -> str:
            key, default = match.group(1), match.group(2)
            found = _lookup(variables, key)
            if found is None or isinstance(found, (dict, list)):
                if default is not None:
                    return default
                raise RecipeError(
                    f"no value for ${{{key}}}; set it in Custom data or add a ':-default'"
                )
            return str(found)

        return _VAR.sub(repl, value)
    if isinstance(value, list):
        return [substitute(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, variables) for k, v in value.items()}
    return value


def variables_for(custom: dict[str, Any], recipe_id: str, version: str | None) -> dict[str, Any]:
    return {
        "custom": custom,
        "env": os.environ,
        "recipe": recipe_id,
        "version": version if version is not None else "${version}",
    }
