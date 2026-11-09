"""Typed schemas for recipes (shipped in git) and user settings (kept on this machine).

Compatibility rules:
* Every file carries ``schema_version``. Bump it only for breaking changes and add a migration.
* Settings models allow unknown keys (``extra="allow"``) so values written by a newer version
  or by hand are preserved on save.
* Recipe models forbid unknown keys so typos in ``recipes/sdks.yaml`` fail loudly.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 1

Destination = Literal["onedrive", "play", "testflight"]
DESTINATIONS: tuple[str, ...] = ("onedrive", "play", "testflight")
ArtifactSelection = Literal["demos", "packages", "both"]
ArtifactKind = Literal[
    "apk", "aab", "ipa", "app", "aar", "jar", "zip", "tgz", "xcframework", "framework", "other"
]


class _Settings(BaseModel):
    model_config = ConfigDict(extra="allow", validate_assignment=True)


class _Recipe(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------------------------
# User settings
# --------------------------------------------------------------------------------------------


class RepoEntry(_Settings):
    path: str = ""
    enabled: bool = True


class Tooling(_Settings):
    java_home: str | None = None
    android_sdk: str | None = None
    flutter_bin: str | None = None
    developer_dir: str | None = Field(
        default=None, description="Optional DEVELOPER_DIR to pick a specific Xcode."
    )
    extra_path: list[str] = Field(default_factory=list)


class ReposSettings(_Settings):
    schema_version: int = SCHEMA_VERSION
    repos: dict[str, RepoEntry] = Field(default_factory=dict)
    tooling: Tooling = Field(default_factory=Tooling)

    @field_validator("repos", mode="before")
    @classmethod
    def _accept_plain_paths(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                k: ({"path": v} if isinstance(v, str) or v is None else v) for k, v in value.items()
            }
        return value


class OneDriveOutput(_Settings):
    enabled: bool = False
    root: str | None = None
    demos_android: str = "android"
    demos_ios: str = "ios"
    packages: str = "packages"
    copy_untargeted: bool = Field(
        default=False,
        description="Also copy demo artifacts that have no target id, under <folder>/<recipe-id>/.",
    )


class PlayOutput(_Settings):
    enabled: bool = False
    track: str = "internal"
    release_status: Literal["completed", "draft"] = "completed"
    method: Literal["auto", "api", "fastlane"] = "auto"


class TestFlightOutput(_Settings):
    enabled: bool = False
    method: Literal["altool", "fastlane"] = "altool"


class OutputsSettings(_Settings):
    schema_version: int = SCHEMA_VERSION
    staging: str = "~/sdk-release-kit/staging"
    onedrive: OneDriveOutput = Field(default_factory=OneDriveOutput)
    play: PlayOutput = Field(default_factory=PlayOutput)
    testflight: TestFlightOutput = Field(default_factory=TestFlightOutput)


class SecretRef(_Settings):
    """Points at a secret without containing it."""

    source: Literal["none", "env", "keychain"] = "none"
    name: str = ""

    @property
    def is_set(self) -> bool:
        return self.source != "none" and bool(self.name)


class AndroidKeys(_Settings):
    keystore_path: str | None = None
    key_alias: str | None = None
    store_password: SecretRef = Field(
        default_factory=lambda: SecretRef(source="keychain", name="android.store_password")
    )
    key_password: SecretRef = Field(
        default_factory=lambda: SecretRef(source="keychain", name="android.key_password")
    )
    inject_signing: bool = Field(
        default=True,
        description="Pass signing to Gradle via android.injected.signing.* properties.",
    )


class PlayKeys(_Settings):
    json_key_path: str | None = None


class TestFlightKeys(_Settings):
    key_id: SecretRef = Field(default_factory=lambda: SecretRef(source="env", name="ASC_KEY_ID"))
    issuer_id: SecretRef = Field(
        default_factory=lambda: SecretRef(source="env", name="ASC_ISSUER_ID")
    )
    key_path: str | None = None


class KeysSettings(_Settings):
    schema_version: int = SCHEMA_VERSION
    android: AndroidKeys = Field(default_factory=AndroidKeys)
    play: PlayKeys = Field(default_factory=PlayKeys)
    testflight: TestFlightKeys = Field(default_factory=TestFlightKeys)


# --------------------------------------------------------------------------------------------
# Recipes
# --------------------------------------------------------------------------------------------


class Collect(_Recipe):
    glob: str
    kind: ArtifactKind
    target: str | None = Field(default=None, description="OneDrive target id for this artifact.")
    root: str = Field(
        default="workdir",
        description="Glob root: workdir, repo, or a root the step exposes (e.g. derived_data).",
    )
    required: bool = True


class _StepBase(_Recipe):
    name: str | None = None
    workdir: str = "."
    env: dict[str, str] = Field(default_factory=dict)
    when: list[Destination] = Field(
        default_factory=list,
        description="Run only if one of these destinations is active. Empty means always.",
    )
    host: Literal["any", "macos"] = "any"
    collect: list[Collect] = Field(default_factory=list)


class RunStep(_StepBase):
    type: Literal["run"]
    cmd: str | list[str]


class GradleStep(_StepBase):
    type: Literal["gradle"]
    tasks: list[str]
    args: list[str] = Field(default_factory=lambda: ["--console=plain"])
    sign: bool = True


class FlutterStep(_StepBase):
    type: Literal["flutter"]
    args: list[str]
    sign: bool = True


class _XcodeBase(_StepBase):
    host: Literal["any", "macos"] = "macos"
    workspace: str | None = None
    project: str | None = None
    scheme: str
    pod_install: bool = False

    @model_validator(mode="after")
    def _one_container(self) -> _XcodeBase:
        if self.workspace and self.project:
            raise ValueError("set either workspace or project, not both")
        return self


class XcodeSimulatorStep(_XcodeBase):
    type: Literal["xcode-simulator"]
    configuration: str = "Debug"
    app_name: str | None = Field(default=None, description="Product .app name if not obvious.")
    target: str | None = Field(default=None, description="OneDrive target id for the .app.")


class XcodeIpaStep(_XcodeBase):
    type: Literal["xcode-ipa"]
    configuration: str = "Release"
    export_method: str = "app-store-connect"
    team_id: str | None = None


class XcframeworkStep(_XcodeBase):
    type: Literal["xcframework"]
    framework_name: str
    configuration: str = "Release"


class NpmPackStep(_StepBase):
    type: Literal["npm-pack"]
    install: bool = False


class SourceZipStep(_StepBase):
    type: Literal["source-zip"]
    method: Literal["git-archive", "files"] = "git-archive"
    exclude: list[str] = Field(
        default_factory=lambda: [
            ".git",
            "build",
            ".dart_tool",
            ".gradle",
            "node_modules",
            "Pods",
            ".idea",
            "DerivedData",
        ]
    )


Step = Annotated[
    RunStep
    | GradleStep
    | FlutterStep
    | XcodeSimulatorStep
    | XcodeIpaStep
    | XcframeworkStep
    | NpmPackStep
    | SourceZipStep,
    Field(discriminator="type"),
]


class StoreFlags(_Recipe):
    play: bool = False
    testflight: bool = False
    android_package: str | None = None
    ios_bundle_id: str | None = None


class VersionSource(_Recipe):
    file: str | None = None
    regex: str | None = None
    cmd: list[str] | None = None


class Recipe(_Recipe):
    platform: str
    label: str = ""
    description: str = ""
    version: VersionSource | None = None
    store: StoreFlags = Field(default_factory=StoreFlags)
    demos: list[Step] = Field(default_factory=list)
    packages: list[Step] = Field(default_factory=list)


class OneDriveTarget(_Recipe):
    folder: str = Field(
        description="demos_android, demos_ios or packages (keys of outputs.onedrive)."
    )
    name: str


class RecipesFile(_Recipe):
    schema_version: int = SCHEMA_VERSION
    onedrive_targets: dict[str, OneDriveTarget] = Field(default_factory=dict)
    recipes: dict[str, Recipe] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _targets_exist(self) -> RecipesFile:
        for rid, recipe in self.recipes.items():
            for step in [*recipe.demos, *recipe.packages]:
                targets = [c.target for c in step.collect]
                if isinstance(step, XcodeSimulatorStep):
                    targets.append(step.target)
                for t in targets:
                    if t and t not in self.onedrive_targets:
                        raise ValueError(f"recipe '{rid}' references unknown onedrive target '{t}'")
        return self
