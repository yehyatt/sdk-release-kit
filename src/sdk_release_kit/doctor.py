"""Environment checks that explain confusing build failures before they happen.

Each check returns plain-language warnings; nothing here changes settings or runs a build.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from .models import RecipesFile
from .paths import expand
from .store import Settings

# Gradle added support for running on Java 21 in 8.5.
JAVA21_MIN_GRADLE = (8, 5)


def _java_major(java_home: Path | None) -> int | None:
    if java_home is None:
        return None
    release = java_home / "release"
    if not release.is_file():
        return None
    m = re.search(r'JAVA_VERSION="(\d+)(?:\.(\d+))?', release.read_text(errors="replace"))
    if not m:
        return None
    major = int(m.group(1))
    return int(m.group(2)) if major == 1 and m.group(2) else major  # "1.8" means 8


def _gradle_version(folder: Path) -> tuple[int, int] | None:
    props = folder / "gradle" / "wrapper" / "gradle-wrapper.properties"
    if not props.is_file():
        return None
    m = re.search(r"gradle-(\d+)\.(\d+)", props.read_text(errors="replace"))
    return (int(m.group(1)), int(m.group(2))) if m else None


def _gradle_wrappers(settings: Settings, recipes: RecipesFile) -> dict[str, tuple[int, int]]:
    """Lowest Gradle wrapper version found in each enabled recipe's step workdirs."""
    found: dict[str, tuple[int, int]] = {}
    for rid, recipe in recipes.recipes.items():
        entry = settings.repos.repos.get(rid)
        repo = expand(entry.path) if entry and entry.enabled else None
        if repo is None or not repo.is_dir():
            continue
        folders = {repo, repo / "android"}
        for step in [*recipe.demos, *recipe.packages]:
            wd = repo / step.workdir
            folders |= {wd, wd / "android"}
        versions = [v for f in folders if (v := _gradle_version(f))]
        if versions:
            found[rid] = min(versions)
    return found


def _sandboxed_gradle_home() -> str | None:
    value = os.environ.get("GRADLE_USER_HOME")
    if not value:
        return None
    tmp = os.path.realpath(tempfile.gettempdir())
    real = os.path.realpath(value)
    if "sandbox" in value.lower() or real.startswith(tmp) or "/var/folders/" in real:
        return value
    return None


def environment_warnings(settings: Settings, recipes: RecipesFile) -> list[str]:
    warnings: list[str] = []

    gradle_home = _sandboxed_gradle_home()
    if gradle_home:
        warnings.append(
            f"GRADLE_USER_HOME points at a temporary or sandbox cache ({gradle_home}). IDE and "
            "agent shells often set this and Gradle then fails with confusing cache errors. "
            'Run builds with GRADLE_USER_HOME="$HOME/.gradle" or unset it.'
        )

    java_home = expand(settings.repos.tooling.java_home) or expand(os.environ.get("JAVA_HOME"))
    java = _java_major(java_home)
    if java is not None and java >= 21:
        old = {
            rid: v
            for rid, v in _gradle_wrappers(settings, recipes).items()
            if v < JAVA21_MIN_GRADLE
        }
        for rid, (major, minor) in sorted(old.items()):
            warnings.append(
                f"{rid}: Gradle wrapper {major}.{minor} cannot run on Java {java} ({java_home}); "
                "expect 'Unsupported class file major version'. Point Repositories > Tooling > "
                "java_home at a JDK 17, or upgrade that project's Gradle wrapper to 8.5 or newer."
            )
    return warnings
