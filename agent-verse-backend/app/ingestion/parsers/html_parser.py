"""HTML parser using trafilatura for clean content extraction."""

from __future__ import annotations

import logging
import re

_log = logging.getLogger(__name__)


class HTMLParser:
    """Extract clean article text from HTML using trafilatura.

    Removes navigation, ads, boilerplate. Preserves article structure.
    Falls back to basic tag stripping if trafilatura unavailable.
    """

    def parse(self, content: str, *, url: str = "") -> str:
        # Try trafilatura first (best quality)
        try:
            import trafilatura  # type: ignore[import-not-found]

            text = trafilatura.extract(
                content,
                include_comments=False,
                include_tables=True,
                no_fallback=False,
                favor_recall=True,
            )
            if text and len(text.strip()) > 50:
                return text.strip()
        except ImportError:
            _log.debug("trafilatura not installed")
        except Exception as exc:
            _log.debug("trafilatura_error: %s", exc)

        # Try BeautifulSoup fallback
        try:
            from bs4 import BeautifulSoup  # type: ignore[import-not-found]

            soup = BeautifulSoup(content, "html.parser")
            # Remove script/style tags
            for tag in soup(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            if text:
                return text[:50000]
        except ImportError:
            pass
        except Exception as exc:
            _log.debug("bs4_error: %s", exc)

        # Ultimate fallback: regex tag stripping
        text = re.sub(r"<[^>]+>", " ", content)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:50000]
