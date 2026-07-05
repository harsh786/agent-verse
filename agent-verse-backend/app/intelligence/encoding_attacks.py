"""
Encoding Attack Decoder
========================
Detects and blocks injection attempts that use:
- Unicode homoglyphs (Cyrillic vs Latin lookalikes)
- Leetspeak (1gn0r3 4ll pr3v10us)
- Base64 encoded instructions
- ROT13 / Caesar cipher
- HTML entities (&lt;script&gt;)
- Zero-width characters
- Bidirectional text tricks (RTL override)
"""
from __future__ import annotations

import base64
import codecs
import contextlib
import html
import re
from typing import Any

try:
    from app.observability.logging import get_logger
    logger = get_logger(__name__)
except Exception:
    import logging
    logger = logging.getLogger(__name__)  # type: ignore[assignment]

# Known homoglyph substitutions (Cyrillic, Greek, etc.)
# Keys are Unicode codepoints that look like Latin letters; values are ASCII equivalents.
_HOMOGLYPH_MAP: dict[str, str] = {
    # Cyrillic lookalikes (\u0410=Cyrillic A, \u0430=Cyrillic a, etc.)
    "\u0410": "A", "\u0430": "a",
    "\u0412": "B", "\u0421": "C", "\u0441": "c",
    "\u0435": "e", "\u0415": "E",
    "\u0456": "i", "\u0406": "I",
    "\u043E": "o", "\u041E": "O",
    "\u0440": "r", "\u0420": "R",
    "\u0455": "s", "\u0405": "S",
    "\u0445": "x", "\u0425": "X",
    "\u0443": "y", "\u0423": "Y",
    # Greek lookalikes
    "\u03B1": "a", "\u03C1": "p",
    # Zero-width characters (invisible, used to hide injections)
    "\u200B": "", "\u200C": "", "\u200D": "", "\uFEFF": "",
    # RTL override characters (used to reverse text visually)
    "\u202E": "", "\u202D": "",
}

_LEETSPEAK: dict[str, str] = {
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s",
    "6": "g", "7": "t", "8": "b", "9": "g", "@": "a",
    "$": "s", "!": "i", "+": "t",
}

# Suspicious decoded strings to check
_INJECTION_KEYWORDS: frozenset[str] = frozenset({
    "ignore", "disregard", "forget", "override", "system", "admin",
    "jailbreak", "ignore all previous", "new instructions",
    "you are now", "from now on", "you will",
})


def normalize_homoglyphs(text: str) -> str:
    """Replace homoglyphs with ASCII equivalents."""
    result = []
    for char in text:
        result.append(_HOMOGLYPH_MAP.get(char, char))
    return "".join(result)


def decode_leetspeak(text: str) -> str:
    """Translate leetspeak to plain text."""
    result = []
    for char in text.lower():
        result.append(_LEETSPEAK.get(char, char))
    return "".join(result)


def try_decode_base64(text: str) -> str | None:
    """Try to decode potential base64-encoded content."""
    # Find base64-looking chunks (40+ chars of base64 alphabet)
    b64_pattern = re.compile(r"[A-Za-z0-9+/=]{40,}")
    for match in b64_pattern.finditer(text):
        candidate = match.group(0)
        try:
            decoded = base64.b64decode(candidate + "==").decode("utf-8", errors="ignore")
            if any(kw in decoded.lower() for kw in _INJECTION_KEYWORDS):
                return decoded
        except Exception:
            pass
    return None


def try_decode_rot13(text: str) -> str:
    """Try ROT13 decoding."""
    decoded = codecs.decode(text, "rot13")
    if any(kw in decoded.lower() for kw in _INJECTION_KEYWORDS):
        return decoded
    return ""


def scan_for_encoding_attacks(text: str) -> dict[str, Any]:
    """
    Comprehensive encoding attack scan.
    Returns dict with: clean (bool), attack_type, decoded_content
    """
    if not text:
        return {"clean": True, "attack_type": None}

    # 1. Normalize homoglyphs and check
    normalized = normalize_homoglyphs(text)
    if normalized != text:
        # Check if normalized version contains injection keywords
        norm_lower = normalized.lower()
        for kw in _INJECTION_KEYWORDS:
            if kw in norm_lower:
                with contextlib.suppress(Exception):
                    logger.warning(
                        "homoglyph_injection_detected", keyword=kw, preview=text[:80]
                    )
                return {"clean": False, "attack_type": "homoglyph", "decoded": normalized}

    # 2. HTML entity decode
    html_decoded = html.unescape(text)
    if html_decoded != text:
        for kw in _INJECTION_KEYWORDS:
            if kw in html_decoded.lower():
                return {"clean": False, "attack_type": "html_entity", "decoded": html_decoded}

    # 3. Leetspeak
    leet_decoded = decode_leetspeak(text)
    for kw in _INJECTION_KEYWORDS:
        if kw in leet_decoded and kw not in text.lower():
            with contextlib.suppress(Exception):
                logger.warning("leetspeak_injection_detected", keyword=kw)
            return {"clean": False, "attack_type": "leetspeak", "decoded": leet_decoded}

    # 4. Base64
    b64_decoded = try_decode_base64(text)
    if b64_decoded:
        with contextlib.suppress(Exception):
            logger.warning("base64_injection_detected", preview=b64_decoded[:80])
        return {"clean": False, "attack_type": "base64", "decoded": b64_decoded}

    # 5. ROT13
    rot13 = try_decode_rot13(text)
    if rot13:
        return {"clean": False, "attack_type": "rot13", "decoded": rot13}

    return {"clean": True, "attack_type": None}
