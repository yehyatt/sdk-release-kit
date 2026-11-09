"""Start, find and stop the Settings UI server, and build its sign-in URL.

A running server records ``{pid, port, token}`` in ``<settings>/server.json`` (mode 600, in a
mode 700 directory), so only this user account can read the token. ``sdk-release-kit open``
reuses a running server or starts one in the background, then opens the browser with a
one-time ``?token=`` URL; the server swaps it for an HttpOnly cookie and redirects.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from typing import Any

from . import __version__
from .paths import user_home
from .store._yaml import write_text_atomic

DEFAULT_PORT = 8765


def state_file():
    return user_home() / "server.json"


def log_file():
    return user_home() / "server.log"


def write_state(port: int, token: str) -> None:
    data = {"pid": os.getpid(), "port": port, "token": token, "version": __version__}
    write_text_atomic(state_file(), json.dumps(data))


def clear_state() -> None:
    try:
        data = json.loads(state_file().read_text(encoding="utf-8"))
        if data.get("pid") == os.getpid():
            state_file().unlink()
    except (OSError, ValueError):
        pass


def _read_state() -> dict[str, Any] | None:
    try:
        return json.loads(state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as resp:
            return json.loads(resp.read()).get("app") == "sdk-release-kit"
    except (OSError, ValueError):
        return False


def running() -> dict[str, Any] | None:
    """State of a live server started by this user, or None."""
    state = _read_state()
    if not state or not _pid_alive(int(state.get("pid", 0))):
        return None
    return state if _healthy(int(state["port"])) else None


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def pick_port(preferred: int = DEFAULT_PORT) -> int:
    if _port_free(preferred):
        return preferred
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_background(preferred_port: int = DEFAULT_PORT, timeout: float = 20.0) -> dict[str, Any]:
    port = pick_port(preferred_port)
    user_home().mkdir(parents=True, exist_ok=True)
    os.chmod(user_home(), 0o700)
    fd = os.open(log_file(), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "ab") as log:
        subprocess.Popen(  # noqa: S603 - our own interpreter and module
            [
                sys.executable,
                "-m",
                "sdk_release_kit.cli",
                "serve",
                "--no-browser",
                "--port",
                str(port),
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = running()
        if state and int(state["port"]) == port:
            return state
        time.sleep(0.3)
    raise RuntimeError(f"the Settings UI did not start; see {log_file()}")


def stop() -> bool:
    state = _read_state()
    if not state or not _pid_alive(int(state.get("pid", 0))):
        return False
    os.kill(int(state["pid"]), signal.SIGTERM)
    # uvicorn re-raises SIGTERM after shutdown, so the server cannot always clean up itself.
    state_file().unlink(missing_ok=True)
    return True


def sign_in_url(state: dict[str, Any]) -> str:
    return f"http://127.0.0.1:{state['port']}/?token={state['token']}"


def is_ssh_session() -> bool:
    return bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))
