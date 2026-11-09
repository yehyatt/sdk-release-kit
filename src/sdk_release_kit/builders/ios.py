"""iOS: simulator ``.app`` for the shared folder, archive + IPA export for TestFlight, and
XCFramework packages. All steps need macOS and Xcode.
"""

from __future__ import annotations

import plistlib
import re
import shutil
from pathlib import Path

from ..models import XcframeworkStep, XcodeIpaStep, XcodeSimulatorStep
from ..paths import expand
from ..store.keys import resolve_secret
from .base import BuildContext, Command, Produced, StepError, StepPlan, handler


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-") or "build"


def _container(step) -> list[str]:
    if step.workspace:
        return ["-workspace", step.workspace]
    if step.project:
        return ["-project", step.project]
    return []


def _pod(step, cwd: Path) -> list[Command]:
    return [Command(["pod", "install"], cwd)] if step.pod_install else []


def asc_auth_args(ctx: BuildContext) -> list[str]:
    """Let xcodebuild manage signing with the App Store Connect API key when available."""
    tf = ctx.settings.keys.testflight
    key_path = expand(tf.key_path)
    key_id = resolve_secret(tf.key_id)
    issuer = resolve_secret(tf.issuer_id)
    args = ["-allowProvisioningUpdates"]
    if key_path and key_path.is_file() and key_id and issuer:
        args += [
            "-authenticationKeyPath",
            str(key_path),
            "-authenticationKeyID",
            key_id,
            "-authenticationKeyIssuerID",
            issuer,
        ]
    return args


@handler("xcode-simulator")
def simulator_app(ctx: BuildContext, step: XcodeSimulatorStep, cwd: Path) -> StepPlan:
    derived = ctx.work_dir / "DerivedData" / _slug(step.scheme)
    products = derived / "Build" / "Products" / f"{step.configuration}-iphonesimulator"
    argv = [
        "xcodebuild",
        *_container(step),
        "-scheme",
        step.scheme,
        "-configuration",
        step.configuration,
        "-sdk",
        "iphonesimulator",
        "-destination",
        "generic/platform=iOS Simulator",
        "-derivedDataPath",
        str(derived),
        "CODE_SIGNING_ALLOWED=NO",
        "build",
    ]

    def finish() -> list[Produced]:
        if step.app_name:
            apps = [
                products
                / (step.app_name if step.app_name.endswith(".app") else step.app_name + ".app")
            ]
        else:
            apps = [p for p in sorted(products.glob("*.app")) if "Tests" not in p.name]
        apps = [a for a in apps if a.is_dir()]
        if not apps:
            raise StepError(f"no .app found in {products}; set app_name in the recipe")
        return [Produced(apps[0], "app", step.target)]

    return StepPlan(
        commands=[*_pod(step, cwd), Command(argv, cwd)],
        roots={"derived_data": derived},
        finish=finish,
    )


@handler("xcode-ipa")
def archive_ipa(ctx: BuildContext, step: XcodeIpaStep, cwd: Path) -> StepPlan:
    base = ctx.work_dir / "ipa" / _slug(step.scheme)
    archive = base / f"{_slug(step.scheme)}.xcarchive"
    export_dir = base / "export"
    options = base / "ExportOptions.plist"
    auth = asc_auth_args(ctx)
    team = [f"DEVELOPMENT_TEAM={step.team_id}"] if step.team_id else []

    def prepare() -> None:
        shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True)
        opts = {
            "method": step.export_method,
            "signingStyle": "automatic",
            "destination": "export",
            "uploadSymbols": True,
        }
        if step.team_id:
            opts["teamID"] = step.team_id
        options.write_bytes(plistlib.dumps(opts))

    archive_cmd = Command(
        [
            "xcodebuild",
            *_container(step),
            "-scheme",
            step.scheme,
            "-configuration",
            step.configuration,
            "-destination",
            "generic/platform=iOS",
            "-archivePath",
            str(archive),
            *team,
            *auth,
            "archive",
        ],
        cwd,
    )
    export_cmd = Command(
        [
            "xcodebuild",
            "-exportArchive",
            "-archivePath",
            str(archive),
            "-exportPath",
            str(export_dir),
            "-exportOptionsPlist",
            str(options),
            *auth,
        ],
        cwd,
    )
    return StepPlan(
        commands=[*_pod(step, cwd), archive_cmd, export_cmd],
        prepare=prepare,
        roots={"export": export_dir},
        finish=lambda: [Produced(p, "ipa") for p in sorted(export_dir.glob("*.ipa"))],
    )


@handler("xcframework")
def xcframework(ctx: BuildContext, step: XcframeworkStep, cwd: Path) -> StepPlan:
    base = ctx.work_dir / "xcframework" / _slug(step.scheme)
    out = base / f"{step.framework_name}.xcframework"
    zipped = base / f"{step.framework_name}-{ctx.version}.xcframework.zip"
    commands = list(_pod(step, cwd))
    framework_args: list[str] = []
    for dest, name in (("generic/platform=iOS", "ios"), ("generic/platform=iOS Simulator", "sim")):
        archive = base / f"{name}.xcarchive"
        commands.append(
            Command(
                [
                    "xcodebuild",
                    "archive",
                    *_container(step),
                    "-scheme",
                    step.scheme,
                    "-configuration",
                    step.configuration,
                    "-destination",
                    dest,
                    "-archivePath",
                    str(archive),
                    "SKIP_INSTALL=NO",
                    "BUILD_LIBRARY_FOR_DISTRIBUTION=YES",
                ],
                cwd,
            )
        )
        framework_args += [
            "-framework",
            str(
                archive / "Products" / "Library" / "Frameworks" / f"{step.framework_name}.framework"
            ),
        ]
    commands.append(
        Command(["xcodebuild", "-create-xcframework", *framework_args, "-output", str(out)], cwd)
    )
    commands.append(Command(["ditto", "-c", "-k", "--keepParent", str(out), str(zipped)], cwd))

    def prepare() -> None:
        shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True)

    return StepPlan(
        commands=commands,
        prepare=prepare,
        finish=lambda: [Produced(zipped, "zip")],
    )
