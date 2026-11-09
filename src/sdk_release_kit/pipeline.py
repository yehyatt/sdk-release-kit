"""Orchestration: select recipes, build demos and packages, publish, write a run report.

load recipes (git) + repos/outputs/keys/custom (user)
 -> resolve selected recipe ids to repo roots
 -> for each selection: build demos, build packages into staging/runs/<ts>/<recipe>/
 -> for each ready destination: onedrive copy, play AABs, testflight IPAs
 -> staging/runs/<ts>/report.json + log.txt
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__
from .builders import (
    Artifact,
    BuildContext,
    StepError,
    describe_step,
    execute_step,
    skip_reason,
    step_label,
)
from .builders.android import tooling_env
from .models import DESTINATIONS, Recipe, RecipesFile
from .paths import expand
from .recipes import RecipeError, load_recipes, substitute, variables_for
from .runlog import RunLog
from .store import Settings, load_all
from .store.keys import resolve_secret
from .uploaders import UploadResult, onedrive, play, testflight

STAGES = {"demos": ("demos",), "packages": ("packages",), "both": ("demos", "packages")}


@dataclass
class RunOptions:
    sdks: list[str] | None = None
    artifacts: str = "both"
    destinations: list[str] | None = None
    dry_run: bool = False
    skip_upload: bool = False


@dataclass
class Selection:
    recipe_id: str
    recipe: Recipe
    repo: Path | None = None
    version: str = "unknown"
    commit: str = "unknown"
    error: str | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    status: str = "pending"


def _git(repo: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def resolve_version(recipe: Recipe, repo: Path) -> str:
    src = recipe.version
    value: str | None = None
    if src and src.file and src.regex:
        path = repo / src.file
        if path.is_file():
            match = re.search(src.regex, path.read_text(encoding="utf-8", errors="replace"), re.M)
            if match:
                value = (match.group(1) if match.groups() else match.group(0)).strip()
    elif src and src.cmd:
        try:
            out = subprocess.run(src.cmd, cwd=repo, capture_output=True, text=True, timeout=60)
            value = out.stdout.strip() if out.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            value = None
    value = value or _git(repo, "describe", "--tags", "--always", "--dirty")
    value = value or time.strftime("dev-%Y%m%d-%H%M")
    return re.sub(r"[^A-Za-z0-9._+-]+", "-", value).strip("-") or "unknown"


class Pipeline:
    def __init__(
        self,
        settings: Settings | None = None,
        recipes: RecipesFile | None = None,
        log: RunLog | None = None,
    ) -> None:
        self.settings = settings or load_all()
        self.recipes = recipes or load_recipes()
        self.log = log or RunLog()
        self._register_secrets()

    def _register_secrets(self) -> None:
        """Mask every configured secret in all output, whichever step prints it."""
        k = self.settings.keys
        for ref in (
            k.android.store_password,
            k.android.key_password,
            k.testflight.key_id,
            k.testflight.issuer_id,
        ):
            self.log.add_secret(resolve_secret(ref))

    # Planning --------------------------------------------------------------------------------
    def default_sdks(self) -> list[str]:
        return [
            rid
            for rid, entry in self.settings.repos.repos.items()
            if entry.enabled and entry.path and rid in self.recipes.recipes
        ]

    def select(self, sdks: list[str] | None) -> list[Selection]:
        ids = sdks or self.default_sdks()
        unknown = [s for s in ids if s not in self.recipes.recipes]
        if unknown:
            raise RecipeError(
                f"unknown recipe id(s): {', '.join(unknown)}. "
                f"Known: {', '.join(self.recipes.recipes)}"
            )
        selections = []
        for rid in ids:
            raw = self.recipes.recipes[rid]
            sel = Selection(rid, raw)
            entry = self.settings.repos.repos.get(rid)
            repo = expand(entry.path) if entry else None
            if entry is None or repo is None:
                sel.error = "no checkout path set in Repositories"
            elif not entry.enabled:
                sel.error = "disabled in Repositories"
            elif not repo.is_dir():
                sel.error = f"checkout path does not exist: {repo}"
            else:
                sel.repo = repo
            try:
                pre = Recipe.model_validate(
                    substitute(
                        raw.model_dump(mode="json"), variables_for(self.settings.custom, rid, None)
                    )
                )
                if sel.repo:
                    sel.version = resolve_version(pre, sel.repo)
                    sel.commit = _git(sel.repo, "rev-parse", "--short", "HEAD") or "unknown"
                sel.recipe = Recipe.model_validate(
                    substitute(
                        raw.model_dump(mode="json"),
                        variables_for(self.settings.custom, rid, sel.version),
                    )
                )
            except (RecipeError, ValueError) as exc:
                sel.error = sel.error or str(exc)
            selections.append(sel)
        return selections

    def destination_status(self, opts: RunOptions) -> dict[str, str | None]:
        """Destination -> None if ready, else the reason it will be skipped."""
        o, k = self.settings.outputs, self.settings.keys
        requested = opts.destinations
        if requested is None:
            requested = [d for d in DESTINATIONS if getattr(o, d).enabled]
        bad = [d for d in requested if d not in DESTINATIONS]
        if bad:
            raise ValueError(f"unknown destination(s): {', '.join(bad)}")
        status: dict[str, str | None] = {}
        for dest in requested:
            if opts.skip_upload:
                status[dest] = "--skip-upload"
            elif dest == "onedrive":
                status[dest] = onedrive.readiness(o)
            elif dest == "play":
                status[dest] = play.readiness(o, k)
            else:
                status[dest] = testflight.readiness(o, k)
        return status

    def _context(self, sel: Selection, run_dir: Path, active: set[str]) -> BuildContext:
        staging = expand(self.settings.outputs.staging) or Path("staging")
        assert sel.repo is not None
        return BuildContext(
            recipe_id=sel.recipe_id,
            recipe=sel.recipe,
            repo=sel.repo,
            out_dir=run_dir / sel.recipe_id,
            work_dir=staging / "work" / sel.recipe_id,
            version=sel.version,
            env=tooling_env(dict(os.environ), self.settings.repos.tooling),
            settings=self.settings,
            log=self.log,
            active_destinations=active,
        )

    def describe(self, opts: RunOptions) -> dict[str, Any]:
        """Print and return the plan without building anything."""
        log = self.log
        selections = self.select(opts.sdks)
        dests = self.destination_status(opts)
        active = {d for d, reason in dests.items() if reason is None}
        staging = expand(self.settings.outputs.staging)
        log.section(f"Plan (artifacts: {opts.artifacts})")
        log.info(f"staging: {staging}")
        for dest, reason in dests.items():
            log.info(f"destination {dest}: {'ready' if reason is None else 'SKIP, ' + reason}")
        if not selections:
            log.info("no SDKs selected; set checkout paths in Repositories or pass --sdks")
        for sel in selections:
            log.section(f"{sel.recipe_id} ({sel.recipe.platform})")
            if sel.error:
                log.info(f"ERROR: {sel.error}")
                continue
            log.info(f"repo: {sel.repo}  version: {sel.version}  commit: {sel.commit}")
            ctx = self._context(sel, (staging or Path(".")) / "runs" / "<timestamp>", active)
            for stage in STAGES[opts.artifacts]:
                steps = getattr(sel.recipe, stage)
                log.info(f"{stage}: {len(steps)} step(s)")
                for step in steps:
                    log.info(f"  - {step_label(step)}")
                    try:
                        for line in describe_step(ctx, step):
                            log.info(f"      {line}")
                    except StepError as exc:
                        log.info(f"      ERROR: {exc}")
            store = sel.recipe.store
            if "play" in active:
                log.info(
                    "play: "
                    + (
                        f"upload AAB as {store.android_package}"
                        if store.play and store.android_package
                        else "not uploaded (store.play is false or no android_package)"
                    )
                )
            if "testflight" in active:
                log.info(
                    "testflight: "
                    + (
                        "upload IPA"
                        if store.testflight
                        else "not uploaded (store.testflight is false)"
                    )
                )
        return {
            "dry_run": True,
            "destinations": dests,
            "selections": [
                {
                    "recipe": s.recipe_id,
                    "repo": str(s.repo) if s.repo else None,
                    "version": s.version,
                    "error": s.error,
                }
                for s in selections
            ],
        }

    # Execution -------------------------------------------------------------------------------
    def _bundle_packages(
        self, sel: Selection, ctx: BuildContext, items: list[Artifact]
    ) -> list[Artifact]:
        if len(items) == 1 and items[0].kind in ("zip", "tgz"):
            return items
        if not items:
            return items
        out = ctx.out_dir / "packages" / f"{sel.recipe_id}-{sel.version}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for art in items:
                if art.path.is_dir():
                    for f in sorted(art.path.rglob("*")):
                        if f.is_file():
                            zf.write(f, f"{art.path.name}/{f.relative_to(art.path).as_posix()}")
                else:
                    zf.write(art.path, art.path.name)
        self.log.info(f"bundled {len(items)} package file(s) into {out.name}")
        return [Artifact(out, "zip", sel.recipe_id, "packages", sel.version)]

    def _build(self, sel: Selection, opts: RunOptions, run_dir: Path, active: set[str]) -> None:
        ctx = self._context(sel, run_dir, active)
        ctx.work_dir.mkdir(parents=True, exist_ok=True)
        for stage in STAGES[opts.artifacts]:
            stage_artifacts: list[Artifact] = []
            for step in getattr(sel.recipe, stage):
                reason = skip_reason(ctx, step)
                if reason:
                    self.log.info(f"skip {step_label(step)} ({reason})")
                    continue
                self.log.info(f"{stage}: {step_label(step)}")
                stage_artifacts += execute_step(ctx, step, stage)
            if stage == "packages":
                stage_artifacts = self._bundle_packages(sel, ctx, stage_artifacts)
            sel.artifacts += stage_artifacts

    def _upload(
        self, selections: list[Selection], active: set[str], meta: dict[str, str]
    ) -> list[UploadResult]:
        o, k, log = self.settings.outputs, self.settings.keys, self.log
        results: list[UploadResult] = []
        built = [a for s in selections for a in s.artifacts]

        if "onedrive" in active and built:
            log.section("Publish: shared folder")
            results += onedrive.publish(built, self.recipes, o, log, meta)

        for dest, kind in (("play", "aab"), ("testflight", "ipa")):
            if dest not in active:
                continue
            log.section(f"Publish: {dest}")
            seen: dict[str, str] = {}
            for sel in selections:
                files = [a for a in sel.artifacts if a.kind == kind]
                if not files:
                    continue
                store = sel.recipe.store
                ident = (
                    store.android_package
                    if dest == "play"
                    else (store.ios_bundle_id or sel.recipe_id)
                )
                enabled = store.play if dest == "play" else store.testflight
                for art in files:
                    name = art.path.name
                    if not enabled:
                        results.append(
                            UploadResult(
                                dest, name, "skipped", f"store.{dest} is false for {sel.recipe_id}"
                            )
                        )
                        continue
                    if dest == "play" and not ident:
                        results.append(
                            UploadResult(
                                dest, name, "skipped", "recipe has no store.android_package"
                            )
                        )
                        continue
                    if ident in seen:
                        msg = f"{ident} already uploaded from {seen[ident]} (store collision rule)"
                        results.append(UploadResult(dest, name, "skipped", msg))
                        continue
                    try:
                        if dest == "play":
                            detail = play.upload(art.path, ident, o, k, log)
                        else:
                            detail = testflight.upload(art.path, o, k, log)
                        seen[ident] = sel.recipe_id
                        results.append(UploadResult(dest, name, "ok", detail))
                    except Exception as exc:  # noqa: BLE001 - report and continue
                        results.append(UploadResult(dest, name, "failed", log.redact(str(exc))))
        for r in results:
            if r.status != "ok" or r.destination != "onedrive":
                log.info(f"{r.destination}: {r.status.upper()} {r.artifact} {r.message}".rstrip())
        return results

    def run(self, opts: RunOptions) -> dict[str, Any]:
        if opts.dry_run:
            return self.describe(opts)
        log = self.log
        started = time.strftime("%Y%m%d-%H%M%S")
        staging = expand(self.settings.outputs.staging)
        if staging is None:
            raise ValueError("no staging directory set in Outputs")
        run_dir = staging / "runs" / started
        run_dir.mkdir(parents=True, exist_ok=True)
        log.attach_file(run_dir / "log.txt")
        log.info(f"SDK Release Kit {__version__}, run {started}")

        selections = self.select(opts.sdks)
        dests = self.destination_status(opts)
        active = {d for d, reason in dests.items() if reason is None}
        for dest, reason in dests.items():
            log.info(f"destination {dest}: {'ready' if reason is None else 'SKIP, ' + reason}")

        for sel in selections:
            log.section(f"Build {sel.recipe_id} ({opts.artifacts})")
            if sel.error:
                sel.status = "error"
                log.info(f"ERROR: {sel.error}")
                continue
            log.info(f"repo: {sel.repo}  version: {sel.version}  commit: {sel.commit}")
            try:
                self._build(sel, opts, run_dir, active)
                sel.status = "ok"
            except (StepError, OSError, ValueError) as exc:
                sel.status = "failed"
                sel.error = log.redact(str(exc))
                log.info(f"FAILED: {sel.error}")

        meta = {f"{s.recipe_id}.commit": s.commit for s in selections}
        ok_selections = [s for s in selections if s.status == "ok"]
        uploads = self._upload(ok_selections, active, meta) if active else []

        failed = [s for s in selections if s.status != "ok"] or [
            u for u in uploads if u.status == "failed"
        ]
        report = {
            "tool_version": __version__,
            "run": started,
            "finished": time.strftime("%Y%m%d-%H%M%S"),
            "options": {
                "sdks": [s.recipe_id for s in selections],
                "artifacts": opts.artifacts,
                "destinations": list(dests),
                "skip_upload": opts.skip_upload,
            },
            "destinations": {
                d: ("ready" if r is None else f"skipped: {r}") for d, r in dests.items()
            },
            "builds": [
                {
                    "recipe": s.recipe_id,
                    "status": s.status,
                    "error": s.error,
                    "version": s.version,
                    "commit": s.commit,
                    "artifacts": [a.to_dict() for a in s.artifacts],
                }
                for s in selections
            ],
            "uploads": [u.to_dict() for u in uploads],
            "ok": not failed and bool(selections),
            "run_dir": str(run_dir),
        }
        (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        log.section("Summary")
        for s in selections:
            log.info(
                f"{s.recipe_id}: {s.status} ({len(s.artifacts)} artifact(s))"
                + (f", {s.error}" if s.error else "")
            )
        counts = {
            st: sum(1 for u in uploads if u.status == st) for st in ("ok", "skipped", "failed")
        }
        log.info(
            f"uploads: {counts['ok']} ok, {counts['skipped']} skipped, {counts['failed']} failed"
        )
        log.info(f"report: {run_dir / 'report.json'}")
        return report
