"""Flutter: ``flutter <args>`` from the recipe, with Android signing injected for Gradle."""

from __future__ import annotations

from pathlib import Path

from ..models import FlutterStep
from .android import signing_env
from .base import BuildContext, Command, StepPlan, handler


@handler("flutter")
def flutter_step(ctx: BuildContext, step: FlutterStep, cwd: Path) -> StepPlan:
    builds_android = any(a in ("apk", "appbundle") for a in step.args)
    env = signing_env(ctx) if step.sign and builds_android else {}
    return StepPlan(commands=[Command(["flutter", *step.args], cwd, env)])
