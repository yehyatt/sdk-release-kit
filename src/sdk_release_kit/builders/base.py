"""Step execution framework shared by every platform builder.

A recipe stage (``demos`` or ``packages``) is a list of steps. Each step type has a handler
that turns the step into a ``StepPlan``: commands to run plus how to find what they produced.
Dry runs print the plan; real runs execute it and copy collected artifacts into staging.

To add a step type: write a handler in a builder module, decorate it with ``@handler("name")``,
add the matching model to ``models.Step``, and import the module in ``builders/__init__.py``.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models import Recipe
from ..paths import safe_join
from ..runlog import RunLog
from ..store import Settings


class StepError(RuntimeError):
    pass


@dataclass
class Artifact:
    path: Path
    kind: str
    recipe: str
    stage: str  # "demos" or "packages"
    version: str
    target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "kind": self.kind,
            "recipe": self.recipe,
            "stage": self.stage,
            "version": self.version,
            "target": self.target,
        }


@dataclass
class Command:
    argv: list[str]
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)

    def display(self) -> str:
        return f"(cd {shlex.quote(str(self.cwd))} && {shlex.join(self.argv)})"


@dataclass
class Produced:
    path: Path
    kind: str
    target: str | None = None


@dataclass
class StepPlan:
    commands: list[Command] = field(default_factory=list)
    roots: dict[str, Path] = field(default_factory=dict)
    prepare: Callable[[], None] | None = None
    finish: Callable[[], list[Produced]] | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class BuildContext:
    recipe_id: str
    recipe: Recipe
    repo: Path
    out_dir: Path  # staging/runs/<ts>/<recipe-id>
    work_dir: Path  # staging/work/<recipe-id>, reused across runs (derived data, archives)
    version: str
    env: dict[str, str]
    settings: Settings
    log: RunLog
    active_destinations: set[str]


Handler = Callable[[BuildContext, Any, Path], StepPlan]
HANDLERS: dict[str, Handler] = {}


def handler(step_type: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        HANDLERS[step_type] = fn
        return fn

    return register


def step_label(step: Any) -> str:
    return step.name or f"{step.type} in {step.workdir}"


def skip_reason(ctx: BuildContext, step: Any) -> str | None:
    if step.when and not (set(step.when) & ctx.active_destinations):
        return f"only runs for {', '.join(step.when)}"
    if step.host == "macos" and sys.platform != "darwin":
        return "needs macOS"
    return None


def plan_step(ctx: BuildContext, step: Any) -> tuple[Path, StepPlan]:
    try:
        cwd = safe_join(ctx.repo, step.workdir)
    except ValueError as exc:
        raise StepError(str(exc)) from exc
    fn = HANDLERS.get(step.type)
    if fn is None:
        raise StepError(f"no handler for step type '{step.type}'")
    plan = fn(ctx, step, cwd)
    for cmd in plan.commands:
        cmd.env = {**step.env, **cmd.env}
    return cwd, plan


def run_command(ctx: BuildContext, cmd: Command) -> None:
    env = {**ctx.env, **cmd.env}
    argv0 = cmd.argv[0]
    if os.sep in argv0:
        exe = str((cmd.cwd / argv0).resolve()) if not os.path.isabs(argv0) else argv0
    else:
        exe = shutil.which(argv0, path=env.get("PATH"))
        if exe is None:
            raise StepError(f"'{argv0}' not found on PATH (set it in Repositories > Tooling)")
    ctx.log.info(f"$ {cmd.display()}")
    proc = subprocess.Popen(
        [exe, *cmd.argv[1:]],
        cwd=cmd.cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        errors="replace",
    )
    assert proc.stdout is not None
    for out_line in proc.stdout:
        ctx.log.line(f"    {out_line.rstrip()}")
    code = proc.wait()
    if code != 0:
        raise StepError(f"command failed with exit code {code}: {shlex.join(cmd.argv)}")


def _copy_into(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():
        shutil.rmtree(dest) if dest.is_dir() and not dest.is_symlink() else dest.unlink()
    if src.is_dir():
        shutil.copytree(src, dest, symlinks=True)
    else:
        shutil.copy2(src, dest)
    return dest


def execute_step(ctx: BuildContext, step: Any, stage: str) -> list[Artifact]:
    cwd, plan = plan_step(ctx, step)
    if not cwd.is_dir():
        raise StepError(f"workdir '{step.workdir}' does not exist in {ctx.repo}")
    if plan.prepare:
        plan.prepare()
    for cmd in plan.commands:
        run_command(ctx, cmd)
    produced: list[Produced] = list(plan.finish() if plan.finish else [])

    roots = {"workdir": cwd, "repo": ctx.repo, **plan.roots}
    for spec in step.collect:
        root = roots.get(spec.root)
        if root is None:
            raise StepError(f"collect root '{spec.root}' is not available for {step.type} steps")
        matches = sorted(p for p in root.glob(spec.glob) if p.exists())
        if not matches and spec.required:
            raise StepError(f"no files matched '{spec.glob}' under {root}")
        produced += [Produced(m, spec.kind, spec.target) for m in matches]

    artifacts = []
    stage_dir = ctx.out_dir / stage
    for item in produced:
        dest = _copy_into(item.path, stage_dir)
        ctx.log.info(
            f"collected {item.kind}: {dest.name}" + (f" -> {item.target}" if item.target else "")
        )
        artifacts.append(Artifact(dest, item.kind, ctx.recipe_id, stage, ctx.version, item.target))
    return artifacts


def describe_step(ctx: BuildContext, step: Any) -> list[str]:
    """Lines for a dry run: the commands that would run and what would be collected."""
    reason = skip_reason(ctx, step)
    if reason:
        return [f"skip {step_label(step)} ({reason})"]
    cwd, plan = plan_step(ctx, step)
    lines = [cmd.display() for cmd in plan.commands]
    lines += plan.notes
    for spec in step.collect:
        lines.append(
            f"collect {spec.kind} '{spec.glob}' from {spec.root}"
            + (f" -> {spec.target}" if spec.target else "")
        )
    if not cwd.is_dir():
        lines.append(f"WARNING: workdir '{step.workdir}' does not exist yet")
    return lines
