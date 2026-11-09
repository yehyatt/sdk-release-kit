"""OneDrive or any shared folder, treated as a locally synced directory: we copy files in.

Demos go to ``<root>/<folder>/<name>`` from the recipe's ``onedrive_targets``.
Packages go to ``<root>/<packages>/<recipe-id>/<version>/`` with ``<recipe-id>/LATEST.txt``.
Files are written under a temporary name and renamed, so sync clients never pick up a
half-copied file.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from ..builders import Artifact
from ..models import OutputsSettings, RecipesFile
from ..paths import expand
from ..runlog import RunLog
from . import UploadResult

DEMO_FOLDER_BY_KIND = {
    "apk": "demos_android",
    "aab": "demos_android",
    "ipa": "demos_ios",
    "app": "demos_ios",
}


def readiness(outputs: OutputsSettings) -> str | None:
    od = outputs.onedrive
    if not od.enabled:
        return "disabled in Outputs"
    root = expand(od.root)
    if root is None:
        return "no shared folder root set in Outputs"
    if not root.is_dir():
        return f"shared folder root does not exist: {root}"
    return None


def _subfolder(outputs: OutputsSettings, key: str) -> str:
    value = getattr(outputs.onedrive, key, None)
    if value is None:
        extra = outputs.onedrive.model_extra or {}
        value = extra.get(key, key)
    return str(value)


def _place(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.partial")
    if tmp.exists():
        shutil.rmtree(tmp) if tmp.is_dir() else tmp.unlink()
    if src.is_dir():
        shutil.copytree(src, tmp, symlinks=True)
        if dest.exists():
            shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
        os.replace(tmp, dest)
    else:
        shutil.copy2(src, tmp)
        if dest.is_dir():
            shutil.rmtree(dest)
        os.replace(tmp, dest)


def publish(
    artifacts: list[Artifact],
    recipes: RecipesFile,
    outputs: OutputsSettings,
    log: RunLog,
    meta: dict[str, str],
) -> list[UploadResult]:
    root = expand(outputs.onedrive.root)
    assert root is not None
    results: list[UploadResult] = []

    for art in (a for a in artifacts if a.stage == "demos"):
        if art.target:
            target = recipes.onedrive_targets[art.target]
            dest = root / _subfolder(outputs, target.folder) / target.name
        elif outputs.onedrive.copy_untargeted and art.kind in DEMO_FOLDER_BY_KIND:
            folder = _subfolder(outputs, DEMO_FOLDER_BY_KIND[art.kind])
            dest = root / folder / art.recipe / art.path.name
        else:
            continue
        try:
            _place(art.path, dest)
            log.info(f"onedrive: {art.path.name} -> {dest.relative_to(root)}")
            results.append(UploadResult("onedrive", art.path.name, "ok", location=str(dest)))
        except OSError as exc:
            results.append(UploadResult("onedrive", art.path.name, "failed", str(exc)))

    packages = [a for a in artifacts if a.stage == "packages"]
    for recipe_id in sorted({a.recipe for a in packages}):
        items = [a for a in packages if a.recipe == recipe_id]
        version = items[0].version
        recipe_dir = root / _subfolder(outputs, "packages") / recipe_id
        version_dir = recipe_dir / version
        placed = []
        for art in items:
            try:
                _place(art.path, version_dir / art.path.name)
                placed.append(art.path.name)
                results.append(
                    UploadResult("onedrive", art.path.name, "ok", location=str(version_dir))
                )
            except OSError as exc:
                results.append(UploadResult("onedrive", art.path.name, "failed", str(exc)))
        if placed:
            latest = "\n".join(
                [
                    f"recipe: {recipe_id}",
                    f"version: {version}",
                    f"built_at: {time.strftime('%Y-%m-%dT%H:%M:%S%z')}",
                    f"commit: {meta.get(recipe_id + '.commit', 'unknown')}",
                    f"path: {version}/",
                    "files:",
                    *[f"  - {name}" for name in placed],
                    "",
                ]
            )
            tmp = recipe_dir / ".LATEST.txt.partial"
            tmp.write_text(latest, encoding="utf-8")
            os.replace(tmp, recipe_dir / "LATEST.txt")
            log.info(
                f"onedrive: packages for {recipe_id} {version} -> {version_dir.relative_to(root)}"
            )
    return results
