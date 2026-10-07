"""The prod nginx must never be a tighter upload limit than the backend's own caps."""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import Settings

NGINX = Path(__file__).resolve().parents[2] / "infra" / "nginx.conf"
_UNITS = {"k": 1024, "m": 1024**2, "g": 1024**3}


def _client_max_body_size() -> int:
    match = re.search(r"client_max_body_size\s+(\d+)([kmgKMG]?)\s*;", NGINX.read_text())
    assert match, "infra/nginx.conf sets no client_max_body_size (nginx default is 1 MiB)"
    return int(match.group(1)) * _UNITS.get(match.group(2).lower(), 1)


def test_nginx_allows_the_largest_documented_upload() -> None:
    fields = Settings.model_fields
    largest = max(
        int(fields["knowledge_max_upload_bytes"].default),
        int(fields["ocr_max_upload_bytes"].default),
    )
    assert _client_max_body_size() > largest
