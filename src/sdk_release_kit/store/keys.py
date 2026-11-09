"""Keys layer: paths to key files and references to secrets.

``keys.yaml`` never holds a password. Passwords and tokens are read at run time from an
environment variable or the OS keychain (via ``keyring``), as named by a ``SecretRef``.
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Any

from ..models import KeysSettings, SecretRef
from ..paths import expand, key_files_dir, settings_file
from ._yaml import load_mapping, save_mapping

KEYCHAIN_SERVICE = "sdk-release-kit"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def load() -> KeysSettings:
    return KeysSettings.model_validate(load_mapping(settings_file("keys")))


def save(settings: KeysSettings) -> None:
    save_mapping(settings_file("keys"), settings.model_dump(mode="json", exclude_none=True))


def resolve_secret(ref: SecretRef) -> str | None:
    """Return the secret value or ``None``. Callers must never log the result."""
    if not ref.is_set:
        return None
    if ref.source == "env":
        return os.environ.get(ref.name) or None
    if ref.source == "keychain":
        try:
            import keyring

            return keyring.get_password(KEYCHAIN_SERVICE, ref.name) or None
        except Exception:  # noqa: BLE001 - a broken keyring backend means "not available"
            return None
    return None


def set_keychain_secret(name: str, value: str) -> None:
    import keyring

    keyring.set_password(KEYCHAIN_SERVICE, name, value)


def delete_keychain_secret(name: str) -> None:
    import keyring

    try:
        keyring.delete_password(KEYCHAIN_SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        pass


def store_key_file(filename: str, content_b64: str) -> Path:
    """Save an uploaded key file in the user data dir with mode 600 and return its path."""
    safe = _SAFE_NAME.sub("_", Path(filename).name).strip("._") or "key"
    folder = key_files_dir()
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    target = folder / safe
    data = base64.b64decode(content_b64, validate=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(target, 0o600)
    return target


def _file_status(path: str | None) -> dict[str, Any]:
    p = expand(path)
    if p is None:
        return {"configured": False, "detail": "no path set"}
    if not p.is_file():
        return {"configured": False, "detail": "file not found"}
    return {"configured": True, "detail": "file found"}


def _secret_status(ref: SecretRef) -> dict[str, Any]:
    if not ref.is_set:
        return {"configured": False, "detail": "no reference set"}
    where = f"{ref.source}:{ref.name}"
    if resolve_secret(ref) is None:
        return {"configured": False, "detail": f"{where} is empty"}
    return {"configured": True, "detail": f"{where} is set"}


def status(settings: KeysSettings | None = None) -> dict[str, dict[str, Any]]:
    """Configured or missing, per item, without revealing any value."""
    k = settings or load()
    alias = bool(k.android.key_alias)
    return {
        "android.keystore": _file_status(k.android.keystore_path),
        "android.key_alias": {
            "configured": alias,
            "detail": "alias set" if alias else "no alias set",
        },
        "android.store_password": _secret_status(k.android.store_password),
        "android.key_password": _secret_status(k.android.key_password),
        "play.json_key": _file_status(k.play.json_key_path),
        "testflight.key_id": _secret_status(k.testflight.key_id),
        "testflight.issuer_id": _secret_status(k.testflight.issuer_id),
        "testflight.key_file": _file_status(k.testflight.key_path),
    }
