"""React Native: ``npm pack`` for the package.

RN demo apps reuse the ``run``, ``gradle`` and ``xcode-*`` steps with ``workdir`` pointing at
the demo's ``android/`` and ``ios/`` folders; see ``recipes/sdks.yaml``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..models import NpmPackStep
from .base import BuildContext, Command, Produced, StepPlan, handler


@handler("npm-pack")
def npm_pack(ctx: BuildContext, step: NpmPackStep, cwd: Path) -> StepPlan:
    dest = ctx.work_dir / "npm-pack"
    commands = []
    if step.install:
        commands.append(Command(["npm", "install"], cwd))
    commands.append(Command(["npm", "pack", "--pack-destination", str(dest)], cwd))

    def prepare() -> None:
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True)

    return StepPlan(
        commands=commands,
        prepare=prepare,
        finish=lambda: [Produced(p, "tgz") for p in sorted(dest.glob("*.tgz"))],
    )
