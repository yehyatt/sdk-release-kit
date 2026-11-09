"""Android: Gradle steps, JAVA_HOME / Android SDK environment, and release signing."""

from __future__ import annotations

import os
from pathlib import Path

from ..models import GradleStep
from ..paths import expand
from ..store.keys import resolve_secret
from .base import BuildContext, Command, StepPlan, handler

_INJECT = "ORG_GRADLE_PROJECT_android.injected.signing."


def signing_env(ctx: BuildContext) -> dict[str, str]:
    """Signing properties for the Android Gradle plugin, passed through the environment.

    Using ``ORG_GRADLE_PROJECT_*`` keeps passwords off the command line (and out of ``ps``).
    Returns an empty dict when signing is not configured, so Gradle uses the project's own
    signing setup.
    """
    k = ctx.settings.keys.android
    if not k.inject_signing:
        return {}
    keystore = expand(k.keystore_path)
    if keystore is None or not k.key_alias:
        return {}
    if not keystore.is_file():
        ctx.log.info("WARNING: keystore path is set but the file is missing; not injecting signing")
        return {}
    store_pw = resolve_secret(k.store_password)
    key_pw = resolve_secret(k.key_password) or store_pw
    if not store_pw:
        ctx.log.info("WARNING: keystore password not available; not injecting signing")
        return {}
    ctx.log.add_secret(store_pw)
    ctx.log.add_secret(key_pw)
    return {
        _INJECT + "store.file": str(keystore),
        _INJECT + "store.password": store_pw,
        _INJECT + "key.alias": k.key_alias,
        _INJECT + "key.password": key_pw or store_pw,
    }


def tooling_env(ctx_env: dict[str, str], tooling) -> dict[str, str]:
    """Environment adjustments from Repositories > Tooling. Applied to every step."""
    env = dict(ctx_env)
    path_parts: list[str] = []
    java_home = expand(tooling.java_home)
    if java_home:
        env["JAVA_HOME"] = str(java_home)
        path_parts.append(str(java_home / "bin"))
    sdk = expand(tooling.android_sdk)
    if sdk:
        env["ANDROID_HOME"] = str(sdk)
        env["ANDROID_SDK_ROOT"] = str(sdk)
        path_parts.append(str(sdk / "platform-tools"))
    flutter = expand(tooling.flutter_bin)
    if flutter:
        # Accept the executable, its bin/ folder, or the SDK root.
        if flutter.is_file():
            flutter = flutter.parent
        elif (flutter / "bin" / "flutter").exists():
            flutter = flutter / "bin"
        path_parts.append(str(flutter))
    dev_dir = expand(tooling.developer_dir)
    if dev_dir:
        env["DEVELOPER_DIR"] = str(dev_dir)
    for extra in tooling.extra_path:
        p = expand(extra)
        if p:
            path_parts.append(str(p))
    if path_parts:
        env["PATH"] = os.pathsep.join([*path_parts, env.get("PATH", "")])
    return env


@handler("gradle")
def gradle_step(ctx: BuildContext, step: GradleStep, cwd: Path) -> StepPlan:
    wrapper = "gradlew.bat" if os.name == "nt" else "gradlew"
    exe = f".{os.sep}{wrapper}" if (cwd / wrapper).exists() else "gradle"
    env = signing_env(ctx) if step.sign else {}
    notes = ["(release signing injected from Keys)"] if env else []
    return StepPlan(commands=[Command([exe, *step.tasks, *step.args], cwd, env)], notes=notes)
