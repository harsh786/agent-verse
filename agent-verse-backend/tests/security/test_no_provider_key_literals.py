"""No committed file may contain a provider-key-shaped literal — not even a fake one.

GitHub push protection rejects pushes containing strings shaped like live
provider keys (a fake Stripe key in a redaction test blocked a push on
2026-10-05). Build test secrets from split parts instead, e.g.
``"sk_" "live_" + "X" * 24``.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

_PATTERNS = {
    # What GitHub push protection actually rejected (2026-10-05): a full-length
    # Stripe live/restricted secret key. Short dummies and AWS's documented
    # example id (AKIAIOSFODNN7EXAMPLE) are accepted by GitHub and stay allowed.
    "stripe-live": re.compile(r"\b[sr]k_live_[A-Za-z0-9]{24,}"),
}

_SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".pdf",
    ".zip",
    ".ico",
    ".woff",
    ".woff2",
    ".lock",
}


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [ROOT / p for p in out.splitlines() if Path(p).suffix.lower() not in _SKIP_SUFFIXES]


def test_no_provider_key_shaped_literals_are_committed() -> None:
    offenders: list[str] = []
    for path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, IsADirectoryError):
            continue
        for name, pattern in _PATTERNS.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(ROOT)}:{line} ({name})")
    assert offenders == [], (
        "provider-key-shaped literals would be rejected by GitHub push protection; "
        'split them (e.g. "sk_" "live_..."):\n  ' + "\n  ".join(offenders)
    )
