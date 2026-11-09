"""Platform-neutral steps: ``run`` (any command) and ``source-zip``."""

from __future__ import annotations

import fnmatch
import shlex
import zipfile
from pathlib import Path

from ..models import RunStep, SourceZipStep
from .base import BuildContext, Command, Produced, StepPlan, handler


@handler("run")
def run_step(ctx: BuildContext, step: RunStep, cwd: Path) -> StepPlan:
    argv = shlex.split(step.cmd) if isinstance(step.cmd, str) else list(step.cmd)
    return StepPlan(commands=[Command(argv, cwd)])


# Never packaged by the files method, whatever the recipe says.
SECRET_PATTERNS = [
    ".env",
    ".env.*",
    "*.jks",
    "*.keystore",
    "*.p8",
    "*.p12",
    "*.pem",
    "*.key",
    "*.mobileprovision",
    "key.properties",
    "*signing*.properties",
    "local.properties",
    "*service-account*.json",
    "*adminsdk*.json",
    "client_secret*.json",
    "GoogleService-Info.plist",
    "google-services.json",
    "*.xcconfig.local",
]


def _excluded(rel: Path, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(part, pat) for part in rel.parts for pat in patterns)


@handler("source-zip")
def source_zip(ctx: BuildContext, step: SourceZipStep, cwd: Path) -> StepPlan:
    out = ctx.work_dir / "source-zip" / f"{ctx.recipe_id}-{ctx.version}.zip"
    prefix = f"{ctx.recipe_id}-{ctx.version}/"
    use_git = step.method == "git-archive" and (ctx.repo / ".git").exists()

    def prepare() -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.unlink(missing_ok=True)

    if use_git:
        cmd = Command(
            ["git", "archive", "--format=zip", f"--prefix={prefix}", "-o", str(out), "HEAD", "."],
            cwd,
        )
        return StepPlan(
            commands=[cmd],
            prepare=prepare,
            finish=lambda: [Produced(out, "zip")],
            notes=["(committed files only; uncommitted changes are not included)"],
        )

    def build_zip() -> list[Produced]:
        prepare()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            skipped = 0
            for path in sorted(cwd.rglob("*")):
                rel = path.relative_to(cwd)
                if _excluded(rel, step.exclude):
                    continue
                # Symlinks (often gitignored links into a secrets folder) and anything shaped
                # like a credential are never packaged.
                if path.is_symlink() or any(
                    p.is_symlink() for p in path.parents if cwd in p.parents
                ):
                    skipped += 1
                    continue
                if path.is_file():
                    if _excluded(rel, SECRET_PATTERNS):
                        skipped += 1
                        continue
                    zf.write(path, prefix + rel.as_posix())
        ctx.log.info(f"zipped sources into {out.name}")
        if skipped:
            ctx.log.info(f"left out {skipped} symlink(s) or credential-shaped file(s)")
        return [Produced(out, "zip")]

    return StepPlan(finish=build_zip, notes=[f"zip files under {cwd} excluding {step.exclude}"])
