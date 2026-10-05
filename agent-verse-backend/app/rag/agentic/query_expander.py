"""QueryExpander — generates query variants for multi-source Fusion RAG."""

from __future__ import annotations

import re
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

#: Output budget for the expansion call. 200 tokens let a reasoning model spend
#: the whole budget thinking and return nothing (P0 GOAL-MULTISTEP-RAG); three
#: one-line phrasings need ~60 visible tokens, the rest is reasoning headroom.
EXPANSION_MAX_TOKENS = 1024


class QueryExpander:
    def expand(self, query: str, max_variants: int = 3) -> list[str]:
        variants = [query]
        q = query.lower()
        # Bug fix: chaining .replace("ticket", "issue").replace("issue", "ticket")
        # round-trips any "ticket" query straight back to itself (the second
        # replace also undoes the first), so it silently produced zero new
        # variants for the more common "ticket" phrasing. Handle each
        # direction independently so both synonyms actually get generated.
        if "ticket" in q:
            variants.append(q.replace("ticket", "issue"))
        elif "issue" in q:
            variants.append(q.replace("issue", "ticket"))
        if "find" in q:
            variants.append(q.replace("find", "search for"))
        return list(dict.fromkeys(variants))[:max_variants]

    def expand_for_fusion(self, query: str, max_variants: int = 4) -> list[str]:
        """Generate multiple query phrasings for Fusion RAG (RRF across multiple queries)."""
        variants = [query]
        syns = [
            ("authentication", "login auth"),
            ("flow", "process workflow"),
            ("error", "exception failure"),
            ("deploy", "release launch"),
        ]
        q_lower = query.lower()
        for original, synonyms in syns:
            if original in q_lower:
                for syn in synonyms.split():
                    variants.append(re.sub(original, syn, q_lower, flags=re.I))
        stopwords = {"the", "a", "an", "of", "in", "on", "for", "to", "and", "or"}
        keywords = " ".join(w for w in query.split() if w.lower() not in stopwords)
        if keywords != query:
            variants.append(keywords)
        return list(dict.fromkeys(v for v in variants if v.strip()))[:max_variants]

    async def expand_for_fusion_async(
        self,
        query: str,
        max_variants: int = 4,
        provider: Any = None,
        model: str = "",
        strict: bool = False,
        trace: dict[str, Any] | None = None,
    ) -> list[str]:
        """LLM-driven query expansion for Fusion RAG, with an honest fallback.

        The original query is always ``variants[0]``. When the model returns no
        usable phrasing — an empty completion (a reasoning model that spent its
        budget thinking) or only a preamble — expansion falls back to the
        rule-based variants of the original query, even when ``strict``: the
        original query is a sound retrieval input, and the fallback is recorded
        in ``trace`` (``source="rules"`` + ``fallback_reason``), never silent.
        A provider outage (429, 5xx, circuit open) still raises when
        ``strict``.
        """
        if provider is None:
            _record(trace, source="rules", fallback_reason=None)
            return self.expand_for_fusion(query, max_variants=max_variants)
        from app.core.errors import EmptyCompletionError

        try:
            from app.providers.base import CompletionRequest, Message
            from app.providers.guarded_completion import complete_decision

            resp = await complete_decision(
                provider,
                CompletionRequest(
                    messages=[
                        Message(
                            role="system",
                            content=(
                                "Generate exactly 3 alternative phrasings of this search query. "
                                "Each phrasing should capture the same intent but use different words. "  # noqa: E501
                                "Output one query per line, no numbering, no bullets."
                            ),
                        ),
                        Message(role="user", content=f"Query: {query}"),
                    ],
                    model=model,
                    max_tokens=EXPANSION_MAX_TOKENS,
                    temperature=0.7,
                ),
                role="rag_query_expand",
            )
        except EmptyCompletionError as exc:
            return self._fallback(query, max_variants, trace, "empty_completion", exc)
        except Exception as exc:
            if strict:
                raise
            return self._fallback(query, max_variants, trace, "provider_error", exc)
        raw = str(getattr(resp, "content", "") or "")
        if not raw.strip():
            return self._fallback(query, max_variants, trace, "empty_expansion", None)
        phrasings = parse_phrasings(raw, query)
        if not phrasings:
            return self._fallback(query, max_variants, trace, "invalid_expansion", None)
        _record(trace, source="llm", fallback_reason=None)
        return list(dict.fromkeys([query, *phrasings]))[:max_variants]

    def _fallback(
        self,
        query: str,
        max_variants: int,
        trace: dict[str, Any] | None,
        reason: str,
        exc: BaseException | None,
    ) -> list[str]:
        logger.warning(
            "query_expansion_fallback",
            reason=reason,
            error=str(exc)[:160] if exc is not None else None,
        )
        _record(trace, source="rules", fallback_reason=reason)
        return self.expand_for_fusion(query, max_variants=max_variants)


def _record(trace: dict[str, Any] | None, *, source: str, fallback_reason: str | None) -> None:
    if trace is not None:
        trace["source"] = source
        trace["fallback_reason"] = fallback_reason


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_LIST_MARKER_RE = re.compile(r"^\s*(?:\d+[.)]|[-*\u2022])\s*")


def parse_phrasings(raw: str, query: str) -> list[str]:
    """Usable phrasings from the model output, in order.

    Drops ``<think>`` blocks, list markers and wrapping quotes; skips blank
    lines, preambles ("Here are three phrasings:"), the query itself and
    runaway lines far longer than any rephrasing of the query.
    """
    text = _THINK_RE.sub("", raw)
    max_len = max(300, 4 * len(query))
    original = query.strip().casefold()
    out: list[str] = []
    for line in text.splitlines():
        candidate = _LIST_MARKER_RE.sub("", line).strip().strip("\"'`").strip()
        if not candidate or candidate.endswith(":") or len(candidate) > max_len:
            continue
        if candidate.casefold() == original:
            continue
        out.append(candidate)
    return out
