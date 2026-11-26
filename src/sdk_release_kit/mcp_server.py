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
from typing import Annotated, Any, Literal, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__
from .doctor import environment_warnings
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
   It publishes to the shared folder by default; Play or TestFlight need the user's explicit
   request and allow_store_uploads=true.
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


def read_only(title: str) -> ToolAnnotations:
    return ToolAnnotations(title=title, readOnlyHint=True, openWorldHint=False)


Artifacts = Annotated[
    Literal["demos", "packages", "both"],
    Field(
        description="What to build: 'demos' (demo apps: APK/AAB/IPA), 'packages' (SDK "
        "packages: AAR, XCFramework, pub/npm archives) or 'both'."
    ),
]
SdkIds = Field(
    description="Recipe ids to build, as returned by list_recipes (for example "
    "['flutter', 'android-current'])."
)
DESTINATIONS_HELP = (
    "Where to publish: any of 'onedrive' (the synced shared folder), 'play' "
    "(Google Play internal testing) and 'testflight'."
)


def _log_tail(log: RunLog, limit: int = 300) -> list[str]:
    lines = log.lines
    return lines[-limit:] if len(lines) > limit else lines


@server.tool(annotations=read_only("Get setup status"))
@agent_errors
def get_status() -> dict[str, Any]:
    """Report whether this machine is ready to build and publish. Call this first.

    Returns the settings directory and which settings files exist; each recipe's checkout path,
    whether it is enabled and whether the folder exists; each key as "configured" or "missing"
    (never the value); whether onedrive, play and testflight are ready or why they would be
    skipped; the paths of git, java, flutter, xcodebuild, pod, npm and fastlane on PATH; and
    warnings about the environment (a Java too new for a project's Gradle wrapper, or a
    sandboxed GRADLE_USER_HOME) that would make builds fail in confusing ways.
    Reads local settings only and changes nothing."""
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
        "warnings": environment_warnings(settings, load_recipes()),
    }


@server.tool(annotations=read_only("List build recipes"))
@agent_errors
def list_recipes() -> dict[str, Any]:
    """List every build recipe the kit knows, so you can pick ids for plan_build and run_build.

    For each recipe id: platform (android, ios, flutter, react-native), label, the names of its
    demo and package steps, whether it can go to Play and TestFlight, and the local checkout it
    is pointed at (null when none; fix that with set_repo_path). Also returns the recipe files
    that were loaded. Changes nothing."""
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


@server.tool(annotations=read_only("Plan a build (dry run)"))
@agent_errors
def plan_build(
    sdks: Annotated[
        list[str] | None,
        Field(description="Recipe ids from list_recipes. Omit to plan every enabled recipe."),
    ] = None,
    artifacts: Artifacts = "both",
    destinations: Annotated[
        list[str] | None,
        Field(
            description=DESTINATIONS_HELP
            + " Omit to use the destinations enabled in the Outputs settings."
        ),
    ] = None,
) -> dict[str, Any]:
    """Dry run of a release: shows exactly what run_build would do, without building or uploading.

    Returns the plan lines (each shell command per recipe step, the file globs that would be
    collected), which destinations are ready or skipped and why, and the same report shape as
    run_build. Use it before every run_build and show the plan to the user."""
    log = RunLog()
    result = Pipeline(log=log).run(
        RunOptions(sdks=sdks, artifacts=artifacts, destinations=destinations, dry_run=True)
    )
    return {**result, "plan": log.lines}


@server.tool(
    annotations=ToolAnnotations(
        title="Build and publish", readOnlyHint=False, destructiveHint=False, openWorldHint=True
    )
)
@agent_errors
def run_build(
    sdks: Annotated[list[str], SdkIds],
    artifacts: Artifacts = "both",
    destinations: Annotated[
        list[str] | None,
        Field(description=DESTINATIONS_HELP + " Omit to publish to onedrive only."),
    ] = None,
    skip_upload: Annotated[
        bool, Field(description="Build and collect the files, but publish nowhere.")
    ] = False,
    allow_store_uploads: Annotated[
        bool,
        Field(
            description="Must be true for 'play' or 'testflight'. Set it only when the user "
            "explicitly asked for a store upload in this conversation."
        ),
    ] = False,
) -> dict[str, Any]:
    """Build the given recipes and publish them. Run plan_build first and only call this when
    the user asked for a build. Can take several minutes.

    destinations defaults to ["onedrive"] (the shared folder). "play" and "testflight" reach
    real testers: include them only when the user explicitly asked for a store upload in this
    conversation, and then also set allow_store_uploads=true.

    Returns the run report (artifacts, upload results, ok flag) and the end of the log.
    """
    dests = list(destinations) if destinations is not None else ["onedrive"]
    unknown = [d for d in dests if d not in DESTINATIONS]
    if unknown:
        raise ValueError(f"unknown destination(s): {', '.join(unknown)}")
    stores = [d for d in dests if d in ("play", "testflight")]
    if stores and not allow_store_uploads and not skip_upload:
        raise ValueError(
            f"{', '.join(stores)} upload needs allow_store_uploads=true, and only when the "
            "user explicitly asked for it"
        )
    log = RunLog()
    report = Pipeline(log=log).run(
        RunOptions(sdks=sdks, artifacts=artifacts, destinations=dests, skip_upload=skip_upload)
    )
    return {"report": report, "log_tail": _log_tail(log)}


@server.tool(annotations=read_only("List previous runs"))
@agent_errors
def list_runs(
    limit: Annotated[int, Field(description="How many runs to return, newest first.")] = 10,
) -> list[dict[str, Any]]:
    """List previous build runs, newest first.

    Each entry has the run id (pass it to get_run_report), ok (true, false, or null when the run
    was interrupted before writing a report), the recipe ids requested and each recipe's build
    status. Returns an empty list when nothing has run yet."""
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


@server.tool(annotations=read_only("Get a run report"))
@agent_errors
def get_run_report(
    run: Annotated[str, Field(description="A run id from list_runs.")],
    log_lines: Annotated[
        int, Field(description="How many lines from the end of the run log to include.")
    ] = 200,
) -> dict[str, Any]:
    """Show what happened in one previous run, to explain a failure or find the built files.

    Returns the run's full report (options, each build's status and collected artifacts, each
    upload's result) and the last lines of its log, with secrets already redacted."""
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


@server.tool(
    annotations=ToolAnnotations(
        title="Set a recipe's checkout",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
@agent_errors
def set_repo_path(
    recipe_id: Annotated[str, Field(description="A recipe id from list_recipes.")],
    path: Annotated[
        str, Field(description="Local folder of the SDK checkout; ~ is expanded. Must exist.")
    ],
    enabled: Annotated[
        bool, Field(description="Whether builds that omit sdks include this recipe.")
    ] = True,
) -> dict[str, Any]:
    """Point a recipe at its local checkout and save it in the Repositories settings.

    Building a checkout runs that repository's own build scripts, so only use a path the user
    gave you. Returns the saved path, its resolved absolute form and the enabled flag."""
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
