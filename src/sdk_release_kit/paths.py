"""Resolve where things live.

Nothing in this module may contain a machine-specific absolute path. User data lives in an
OS config directory (or ``SDK_RELEASE_KIT_HOME``); shipped data lives next to the package.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "sdk-release-kit"
SETTINGS_KINDS = ("repos", "outputs", "keys", "custom")

_PACKAGE_DIR = Path(__file__).resolve().parent


def user_home() -> Path:
    """Directory holding this machine's settings. Never inside the git checkout."""
    env = os.environ.get("SDK_RELEASE_KIT_HOME")
    if env:
        return Path(env).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_NAME


def settings_file(kind: str) -> Path:
    """Path of one settings layer. ``SDK_RELEASE_KIT_<KIND>`` overrides it with a file path."""
    if kind not in SETTINGS_KINDS:
        raise ValueError(f"unknown settings kind: {kind}")
    env = os.environ.get(f"SDK_RELEASE_KIT_{kind.upper()}")
    if env:
        return Path(env).expanduser()
    return user_home() / f"{kind}.yaml"


def key_files_dir() -> Path:
    """Where key files uploaded through the Settings UI are stored (mode 700)."""
    return user_home() / "keys"


def recipe_overlay_dir() -> Path:
    """Machine-local recipe overlays, deep-merged over the shipped recipes."""
    return user_home() / "recipes.d"


def data_dir(name: str) -> Path:
    """Shipped data (``recipes``, ``web``, ``examples``).

    A wheel install carries them in ``sdk_release_kit/_data``; an editable install reads them
    from the repository root.
    """
    packaged = _PACKAGE_DIR / "_data" / name
    if packaged.is_dir():
        return packaged
    repo_root = _PACKAGE_DIR.parent.parent
    candidate = repo_root / name
    if candidate.is_dir():
        return candidate
    raise FileNotFoundError(f"shipped data directory '{name}' not found")


def expand(path: str | os.PathLike[str] | None) -> Path | None:
    """Expand ``~`` and ``$VARS`` in a user-supplied path. Empty values become ``None``."""
    if path is None:
        return None
    text = str(path).strip()
    if not text:
        return None
    return Path(os.path.expandvars(os.path.expanduser(text)))


def safe_join(root: Path, relative: str) -> Path:
    """Join a recipe-relative path to a repo root and refuse to leave the root."""
    rel = Path(relative)
    if rel.is_absolute():
        raise ValueError(f"recipe paths must be relative, got '{relative}'")
    joined = (root / rel).resolve()
    root_resolved = root.resolve()
    if joined != root_resolved and root_resolved not in joined.parents:
        raise ValueError(f"recipe path '{relative}' escapes the repository root")
    return joined
