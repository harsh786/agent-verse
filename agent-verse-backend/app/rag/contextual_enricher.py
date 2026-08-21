"""Contextual Chunk Enricher — Anthropic-style contextual RAG.

Each chunk is prepended with a short document-level context before embedding.
This dramatically improves retrieval recall for chunks that make no sense
without surrounding context (e.g. "He agreed." without knowing who or what).

Reference: Anthropic's Contextual Retrieval blog post (2024).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.providers.base import LLMProvider

_CONTEXT_PREFIX_TEMPLATE = "[Doc context: {summary}]\n\n"
_MAX_SUMMARY_CHARS = 200


class ContextualChunkEnricher:
    """Enriches chunks by prepending document-level context before embedding.

    Two modes:
    - Fast: use a pre-supplied summary string.
    - LLM: generate a per-chunk contextual prefix via the language model.
    """

    def enrich(self, chunks: list[str], document_summary: str) -> list[str]:
        """Prepend *document_summary* to each chunk (fast, no LLM).

        Parameters
        ----------
        chunks : list[str]
            Raw chunk texts.
        document_summary : str
            A short summary of the full document (1-3 sentences).
        """
        if not document_summary.strip():
            return chunks
        prefix = _CONTEXT_PREFIX_TEMPLATE.format(
            summary=document_summary.strip()[:_MAX_SUMMARY_CHARS]
        )
        return [f"{prefix}{chunk}" for chunk in chunks]

    async def summarize_document(self, content: str, provider: LLMProvider) -> str:
        """Generate a 1-paragraph document summary using the LLM.

        Returns an empty string if the call fails, so callers can fall back
        to the fast mode.
        """
        try:
            from app.providers.base import CompletionRequest, Message

            truncated = content[:3000]  # stay within context limit
            req = CompletionRequest(
                messages=[
                    Message(
                        role="user",
                        content=(
                            "Write a one-sentence summary of the following document "
                            "that captures the main topic and key entities. "
                            "Max 100 words.\n\n" + truncated
                        ),
                    )
                ],
                model="",
                max_tokens=120,
                temperature=0.0,
            )
            resp = await provider.complete(req)
            return (resp.content or "").strip()[:_MAX_SUMMARY_CHARS]
        except Exception:
            return ""

    async def enrich_with_llm(
        self,
        chunks: list[str],
        full_content: str,
        provider: LLMProvider,
    ) -> list[str]:
        """Generate a per-chunk contextual prefix via the LLM.

        Each prefix explains what this chunk is about within the context of
        the full document, à la Anthropic's contextual retrieval approach.
        Falls back to `enrich(chunks, summary)` on LLM error.
        """
        try:
            from app.providers.base import CompletionRequest, Message

            summary = await self.summarize_document(full_content, provider)
            if not summary:
                return chunks

            enriched: list[str] = []
            doc_excerpt = full_content[:1000]
            for chunk in chunks:
                try:
                    req = CompletionRequest(
                        messages=[
                            Message(
                                role="user",
                                content=(
                                    "Given the document excerpt below, write a SHORT one-sentence "
                                    "context (max 50 words) that situates the following chunk "
                                    "within the document. Only the context sentence, nothing else.\n\n"  # noqa: E501
                                    f"Document excerpt:\n{doc_excerpt}\n\n"
                                    f"Chunk:\n{chunk[:500]}"
                                ),
                            )
                        ],
                        model="",
                        max_tokens=80,
                        temperature=0.0,
                    )
                    resp = await provider.complete(req)
                    context = (resp.content or "").strip()[:150]
                    prefix = f"[Context: {context}]\n\n"
                    enriched.append(f"{prefix}{chunk}")
                except Exception:
                    enriched.append(chunk)
            return enriched
        except Exception:
            return chunks
