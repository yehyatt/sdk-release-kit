"""Google Play internal testing upload for AABs.

Method ``api`` uses the Google Play Developer API (``pip install 'sdk-release-kit[play]'``).
Method ``fastlane`` shells out to ``fastlane supply``. ``auto`` prefers the API library and
falls back to fastlane. Missing credentials or tools mean a soft skip, never a failed run.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

from ..models import KeysSettings, OutputsSettings
from ..paths import expand
from ..runlog import RunLog


def _method(outputs: OutputsSettings) -> str | None:
    wanted = outputs.play.method
    has_api = importlib.util.find_spec("googleapiclient") is not None
    has_fastlane = shutil.which("fastlane") is not None
    if wanted in ("auto", "api") and has_api:
        return "api"
    if wanted in ("auto", "fastlane") and has_fastlane:
        return "fastlane"
    return None


def readiness(outputs: OutputsSettings, keys: KeysSettings) -> str | None:
    if not outputs.play.enabled:
        return "disabled in Outputs"
    key = expand(keys.play.json_key_path)
    if key is None:
        return "no Play service account JSON set in Keys"
    if not key.is_file():
        return f"Play service account JSON not found: {key}"
    if _method(outputs) is None:
        return (
            "no upload method: install the API extra (pip install 'sdk-release-kit[play]') "
            "or fastlane"
        )
    return None


def upload(
    aab: Path, package: str, outputs: OutputsSettings, keys: KeysSettings, log: RunLog
) -> str:
    """Upload one AAB; return a short description. Raises on failure."""
    key = expand(keys.play.json_key_path)
    assert key is not None
    track = outputs.play.track
    status = outputs.play.release_status
    method = _method(outputs)
    if method == "api":
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        creds = service_account.Credentials.from_service_account_file(
            str(key), scopes=["https://www.googleapis.com/auth/androidpublisher"]
        )
        svc = build("androidpublisher", "v3", credentials=creds, cache_discovery=False)
        edits = svc.edits()
        edit_id = edits.insert(packageName=package, body={}).execute()["id"]
        media = MediaFileUpload(str(aab), mimetype="application/octet-stream", resumable=True)
        bundle = (
            edits.bundles().upload(packageName=package, editId=edit_id, media_body=media).execute()
        )
        code = str(bundle["versionCode"])
        edits.tracks().update(
            packageName=package,
            editId=edit_id,
            track=track,
            body={"track": track, "releases": [{"versionCodes": [code], "status": status}]},
        ).execute()
        edits.commit(packageName=package, editId=edit_id).execute()
        return f"{package} versionCode {code} on {track} ({status})"

    argv = [
        "fastlane",
        "supply",
        "--aab",
        str(aab),
        "--package_name",
        package,
        "--track",
        track,
        "--release_status",
        status,
        "--json_key",
        str(key),
        "--skip_upload_metadata",
        "true",
        "--skip_upload_images",
        "true",
        "--skip_upload_screenshots",
        "true",
        "--skip_upload_changelogs",
        "true",
    ]
    proc = subprocess.run(argv, capture_output=True, text=True)
    for line in (proc.stdout + proc.stderr).splitlines():
        log.line(f"    {line}")
    if proc.returncode != 0:
        raise RuntimeError(f"fastlane supply exited with {proc.returncode}")
    return f"{package} on {track} ({status}) via fastlane"
