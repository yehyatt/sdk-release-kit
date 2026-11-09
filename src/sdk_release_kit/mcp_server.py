"""MCP server: lets AI agents inspect, plan and run SDK releases.

Start with ``sdk-release-kit mcp`` (stdio). Nothing here returns a secret value: key status is
reported as configured or missing, and run logs pass through the same redactor as the CLI.
Stdout belongs to the MCP protocol, so this module must never print.
"""

from __future__ import annotations

import functools
import json
import shutil
from collections.abc import Callable
from typing import Any, Literal, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__
from .models import DESTINATIONS, RepoEntry
from .paths import expand, settings_file, user_home
from .pipeline import Pipeline, RunOptions
from .recipes import load_recipes, recipe_sources
from .runlog import RunLog
from .store import keys as keys_store
from .store import load_all
from .store import repos as repos_store

INSTRUCTIONS = """\
SDK Release Kit builds mobile SDK demo apps and SDK packages (Android, iOS, Flutter,
React Native) from local checkouts and publishes them to a synced shared folder,
Google Play internal testing and TestFlight.

Suggested flow:
1. get_status: see which recipes have a checkout, which keys are configured and which
   destinations are ready. Secret values are never returned.
2. list_recipes: recipe ids and what each builds.
3. plan_build: a dry run that shows the exact commands, collected files and destinations.
4. run_build: only when the user asked for a build or release. Builds can take minutes.
5. list_runs / get_run_report: inspect previous runs.

Store uploads soft-skip when credentials are missing. Recipes live in git; machine paths
and keys live in the user's settings directory. Ask the user to fill keys in the Settings UI
(`sdk-release-kit serve`); never ask them to paste passwords into the chat.
"""

server = MCPServer(
    name="sdk-release-kit",
    title="SDK Release Kit",
    instructions=INSTRUCTIONS,
    version=__version__,
    website_url="https://github.com/yehyatt/sdk-release-kit",
)

F = TypeVar("F", bound=Callable[..., Any])


def agent_errors(fn: F) -> F:
    """Turn expected failures into ToolError so the agent sees the reason, not a generic error."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (ValueError, OSError) as exc:  # RecipeError is a ValueError
            raise ToolError(str(exc)) from exc

    return wrapper  # type: ignore[return-value]


READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
Artifacts = Literal["demos", "packages", "both"]


def _log_tail(log: RunLog, limit: int = 300) -> list[str]:
    lines = log.lines
    return lines[-limit:] if len(lines) > limit else lines


@server.tool(annotations=READ_ONLY)
@agent_errors
def get_status() -> dict[str, Any]:
    """Settings files, repository checkouts, key status (configured or missing, never values),
    destination readiness and build tools found on PATH."""
    settings = load_all()
    pipeline = Pipeline(settings=settings)
    repos = {}
    for rid, entry in settings.repos.repos.items():
        p = expand(entry.path)
        repos[rid] = {
            "path": entry.path,
            "enabled": entry.enabled,
            "exists": bool(p and p.is_dir()),
        }
    destinations = pipeline.destination_status(RunOptions(destinations=list(DESTINATIONS)))
    return {
        "version": __version__,
        "settings_dir": str(user_home()),
        "settings_files": {
            k: {"path": str(settings_file(k)), "exists": settings_file(k).exists()}
            for k in ("repos", "outputs", "keys", "custom")
        },
        "repos": repos,
        "keys": keys_store.status(settings.keys),
        "destinations": {
            d: ("ready" if reason is None else f"skipped: {reason}")
            for d, reason in destinations.items()
        },
        "tools": {
            t: shutil.which(t)
            for t in ("git", "java", "flutter", "xcodebuild", "pod", "npm", "fastlane")
        },
        "staging": str(expand(settings.outputs.staging)),
    }


@server.tool(annotations=READ_ONLY)
@agent_errors
def list_recipes() -> dict[str, Any]:
    """Recipe ids with platform, steps and store flags, plus whether each has a checkout."""
    recipes = load_recipes()
    repos = load_all().repos.repos
    out = {}
    for rid, r in recipes.recipes.items():
        entry = repos.get(rid)
        out[rid] = {
            "platform": r.platform,
            "label": r.label,
            "demo_steps": [s.name or s.type for s in r.demos],
            "package_steps": [s.name or s.type for s in r.packages],
            "store": {"play": r.store.play, "testflight": r.store.testflight},
            "checkout": entry.path if entry else None,
            "enabled": bool(entry and entry.enabled),
        }
    return {"sources": [str(s) for s in recipe_sources()], "recipes": out}


@server.tool(annotations=READ_ONLY)
@agent_errors
def plan_build(
    sdks: list[str] | None = None,
    artifacts: Artifacts = "both",
    destinations: list[str] | None = None,
) -> dict[str, Any]:
    """Dry run. Shows the commands that would run, the files that would be collected and
    which destinations are ready or skipped. Builds nothing.

    sdks: recipe ids; default is every enabled repository.
    destinations: subset of onedrive, play, testflight; default is those enabled in Outputs.
    """
    log = RunLog()
    result = Pipeline(log=log).run(
        RunOptions(sdks=sdks, artifacts=artifacts, destinations=destinations, dry_run=True)
    )
    return {**result, "plan": log.lines}


@server.tool(annotations=ToolAnnotations(destructiveHint=False, openWorldHint=True))
@agent_errors
def run_build(
    sdks: list[str],
    artifacts: Artifacts = "both",
    destinations: list[str] | None = None,
    skip_upload: bool = False,
) -> dict[str, Any]:
    """Build the given recipes and publish to the given destinations. Run plan_build first
    and only call this when the user asked for a build. Can take several minutes. Uploads to
    Google Play or TestFlight reach real testers.

    Returns the run report (artifacts, upload results, ok flag) and the end of the log.
    """
    log = RunLog()
    report = Pipeline(log=log).run(
        RunOptions(
            sdks=sdks, artifacts=artifacts, destinations=destinations, skip_upload=skip_upload
        )
    )
    return {"report": report, "log_tail": _log_tail(log)}


@server.tool(annotations=READ_ONLY)
@agent_errors
def list_runs(limit: int = 10) -> list[dict[str, Any]]:
    """Most recent runs with their outcome."""
    staging = expand(load_all().outputs.staging)
    runs_dir = staging / "runs" if staging else None
    if not runs_dir or not runs_dir.is_dir():
        return []
    out = []
    for run in sorted((p for p in runs_dir.iterdir() if p.is_dir()), reverse=True)[:limit]:
        report_file = run / "report.json"
        if report_file.exists():
            report = json.loads(report_file.read_text(encoding="utf-8"))
            out.append(
                {
                    "run": run.name,
                    "ok": report.get("ok"),
                    "sdks": report.get("options", {}).get("sdks"),
                    "builds": {b["recipe"]: b["status"] for b in report.get("builds", [])},
                }
            )
        else:
            out.append({"run": run.name, "ok": None, "note": "no report (interrupted run?)"})
    return out


@server.tool(annotations=READ_ONLY)
@agent_errors
def get_run_report(run: str, log_lines: int = 200) -> dict[str, Any]:
    """Full report.json and the last lines of log.txt for one run id from list_runs."""
    staging = expand(load_all().outputs.staging)
    if staging is None or "/" in run or run.startswith("."):
        raise ValueError("invalid run id")
    run_dir = staging / "runs" / run
    if not run_dir.is_dir():
        raise ValueError(f"no run named {run}")
    report_file, log_file = run_dir / "report.json", run_dir / "log.txt"
    report = json.loads(report_file.read_text(encoding="utf-8")) if report_file.exists() else None
    lines = log_file.read_text(encoding="utf-8").splitlines() if log_file.exists() else []
    return {"report": report, "log_tail": lines[-log_lines:]}


@server.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
@agent_errors
def set_repo_path(recipe_id: str, path: str, enabled: bool = True) -> dict[str, Any]:
    """Point a recipe at its local checkout (Repositories settings). The folder must exist."""
    if recipe_id not in load_recipes().recipes:
        raise ValueError(f"unknown recipe id {recipe_id}")
    resolved = expand(path)
    if resolved is None or not resolved.is_dir():
        raise ValueError(f"folder not found: {path}")
    settings = repos_store.load()
    settings.repos[recipe_id] = RepoEntry(path=path, enabled=enabled)
    repos_store.save(settings)
    return {"recipe": recipe_id, "path": path, "resolved": str(resolved), "enabled": enabled}


def run() -> None:
    server.run("stdio")
