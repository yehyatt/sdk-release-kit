"""Destinations. Each uploader exposes ``readiness(...) -> str | None`` (``None`` means ready,
otherwise the reason it will be skipped) and an upload/publish function returning results.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


@dataclass
class UploadResult:
    destination: str
    artifact: str
    status: Literal["ok", "skipped", "failed"]
    message: str = ""
    location: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)
