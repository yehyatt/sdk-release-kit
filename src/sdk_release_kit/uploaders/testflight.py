"""TestFlight upload for IPAs with an App Store Connect API key (key id, issuer id, .p8).

``altool`` (default) ships with Xcode. ``fastlane`` uses ``fastlane pilot``. The key file is
staged into a private temporary directory under the name the tools expect, then removed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ..models import KeysSettings, OutputsSettings
from ..paths import expand
from ..runlog import RunLog
from ..store.keys import resolve_secret


def readiness(outputs: OutputsSettings, keys: KeysSettings) -> str | None:
    if not outputs.testflight.enabled:
        return "disabled in Outputs"
    tf = keys.testflight
    if not resolve_secret(tf.key_id):
        return f"App Store Connect key id not available ({tf.key_id.source}:{tf.key_id.name})"
    if not resolve_secret(tf.issuer_id):
        return (
            f"App Store Connect issuer id not available ({tf.issuer_id.source}:{tf.issuer_id.name})"
        )
    key = expand(tf.key_path)
    if key is None or not key.is_file():
        return "App Store Connect .p8 key file not set or missing in Keys"
    if outputs.testflight.method == "altool" and (
        sys.platform != "darwin" or not shutil.which("xcrun")
    ):
        return "altool needs macOS with Xcode"
    if outputs.testflight.method == "fastlane" and not shutil.which("fastlane"):
        return "fastlane not installed"
    return None


def _run(argv: list[str], env: dict[str, str], log: RunLog) -> None:
    proc = subprocess.run(argv, capture_output=True, text=True, env=env)
    for line in (proc.stdout + proc.stderr).splitlines():
        log.line(f"    {line}")
    if proc.returncode != 0:
        raise RuntimeError(f"{argv[0]} {argv[1]} exited with {proc.returncode}")


def upload(ipa: Path, outputs: OutputsSettings, keys: KeysSettings, log: RunLog) -> str:
    tf = keys.testflight
    key_id = resolve_secret(tf.key_id) or ""
    issuer = resolve_secret(tf.issuer_id) or ""
    key_path = expand(tf.key_path)
    assert key_path is not None
    with tempfile.TemporaryDirectory(prefix="srk-asc-") as tmp:
        os.chmod(tmp, 0o700)
        env = dict(os.environ)
        if outputs.testflight.method == "fastlane":
            api_json = Path(tmp) / "asc.json"
            content = key_path.read_text(encoding="utf-8")
            log.add_secret(content)
            api_json.write_text(json.dumps({"key_id": key_id, "issuer_id": issuer, "key": content}))
            os.chmod(api_json, 0o600)
            _run(
                [
                    "fastlane",
                    "pilot",
                    "upload",
                    "--ipa",
                    str(ipa),
                    "--api_key_path",
                    str(api_json),
                    "--skip_waiting_for_build_processing",
                    "true",
                ],
                env,
                log,
            )
            return f"{ipa.name} uploaded via fastlane pilot"
        staged = Path(tmp) / f"AuthKey_{key_id}.p8"
        shutil.copy2(key_path, staged)
        os.chmod(staged, 0o600)
        env["API_PRIVATE_KEYS_DIR"] = tmp
        _run(
            [
                "xcrun",
                "altool",
                "--upload-app",
                "-f",
                str(ipa),
                "-t",
                "ios",
                "--apiKey",
                key_id,
                "--apiIssuer",
                issuer,
            ],
            env,
            log,
        )
        return f"{ipa.name} uploaded via altool"
