"""Run log with secret redaction and live listeners (CLI stdout, Settings UI stream)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

REDACTED = "********"


class RunLog:
    def __init__(self) -> None:
        self._secrets: set[str] = set()
        self._lines: list[str] = []
        self._file: Path | None = None
        self._listeners: list[Callable[[str], None]] = []
        self._lock = threading.Lock()

    # Secrets ---------------------------------------------------------------------------------
    def add_secret(self, value: str | None) -> None:
        """Register a value that must never appear in output."""
        if value and len(value) >= 3:
            self._secrets.add(value)

    def redact(self, text: str) -> str:
        for secret in sorted(self._secrets, key=len, reverse=True):
            text = text.replace(secret, REDACTED)
        return text

    # Output ----------------------------------------------------------------------------------
    def subscribe(self, listener: Callable[[str], None]) -> None:
        self._listeners.append(listener)

    def attach_file(self, path: Path) -> None:
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("".join(f"{line}\n" for line in self._lines), encoding="utf-8")
            self._file = path

    def line(self, text: str) -> None:
        clean = self.redact(text.rstrip("\n"))
        with self._lock:
            self._lines.append(clean)
            if self._file:
                with self._file.open("a", encoding="utf-8") as fh:
                    fh.write(clean + "\n")
        for listener in list(self._listeners):
            listener(clean)

    def info(self, text: str) -> None:
        self.line(f"[{time.strftime('%H:%M:%S')}] {text}")

    def section(self, text: str) -> None:
        self.line("")
        self.info(f"== {text}")

    @property
    def lines(self) -> list[str]:
        with self._lock:
            return list(self._lines)
