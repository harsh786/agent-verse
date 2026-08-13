"""Late Chunking — embed the full document, then slice embeddings per chunk.

Traditional RAG embeds each chunk independently. Late chunking embeds the
whole document (or large passage) to get token-level embeddings that encode
the full context, then averages token embeddings within each chunk boundary.
This dramatically improves retrieval for ambiguous pronouns and references.

If the provider does not expose token-level embeddings (most don't), this
module falls back to returning `None` so the caller can use standard embedding.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LateChunk:
    content: str
    embedding: list[float]
    char_start: int
    char_end: int


class LateChunker:
    """Embed full document and slice per-chunk embeddings from token embeddings.

    Most commercial providers do NOT expose token-level embeddings (they only
    return a single sentence embedding). This class provides:

    1. `chunk_and_embed_with_token_embeddings(...)` — the true late chunking
       approach, requiring a provider that returns per-token embeddings.

    2. `chunk_and_embed_standard(...)` — fallback that uses the provider's
       standard sentence embedding for each chunk (same as traditional RAG).

    Call `is_supported(provider)` to check which path to use.
    """

    @staticmethod
    def is_supported(provider: object) -> bool:
        """Return True if the provider exposes token-level embeddings."""
        return hasattr(provider, "embed_tokens") and callable(getattr(provider, "embed_tokens", None))

    async def chunk_and_embed(
        self,
        content: str,
        chunks: list[str],
        provider: object,
    ) -> list[LateChunk] | None:
        """Embed chunks using late chunking if supported; else return None.

        Parameters
        ----------
        content : str
            Full document text (used to compute document-level embeddings).
        chunks : list[str]
            Pre-split chunk texts.
        provider : object
            An embedding provider.

        Returns
        -------
        list[LateChunk] | None
            Late chunks with embeddings, or None if not supported by provider.
        """
        if not self.is_supported(provider):
            return None
        try:
            return await self._late_chunk(content, chunks, provider)
        except Exception:
            return None

    async def chunk_and_embed_standard(
        self,
        chunks: list[str],
        provider: object,
    ) -> list[LateChunk]:
        """Fallback: embed each chunk independently (standard RAG embedding)."""
        from app.providers.base import embed_texts

        embeddings = await embed_texts(chunks, provider=provider)
        result: list[LateChunk] = []
        offset = 0
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            result.append(LateChunk(
                content=chunk,
                embedding=emb,
                char_start=offset,
                char_end=offset + len(chunk),
            ))
            offset += len(chunk) + 1  # +1 for separator
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _late_chunk(
        self,
        content: str,
        chunks: list[str],
        provider: object,
    ) -> list[LateChunk]:
        """True late chunking: get token embeddings then average per chunk.

        This path is only reached when `provider.embed_tokens` exists.
        """
        # Get token-level embeddings for the full document
        token_embeddings: list[list[float]] = await provider.embed_tokens(content)  # type: ignore[attr-defined]
        if not token_embeddings:
            return []

        dim = len(token_embeddings[0]) if token_embeddings else 0
        total_tokens = len(token_embeddings)

        # Assign tokens to chunks proportionally by character length
        total_chars = sum(len(c) for c in chunks)
        result: list[LateChunk] = []
        token_offset = 0
        char_offset = 0

        for chunk in chunks:
            chunk_chars = len(chunk)
            # Proportion of tokens for this chunk
            if total_chars > 0:
                chunk_token_count = max(1, round((chunk_chars / total_chars) * total_tokens))
            else:
                chunk_token_count = 1

            end_token = min(token_offset + chunk_token_count, total_tokens)
            span = token_embeddings[token_offset:end_token]
            if span:
                avg_emb = [sum(v[d] for v in span) / len(span) for d in range(dim)]
            else:
                avg_emb = [0.0] * dim

            result.append(LateChunk(
                content=chunk,
                embedding=avg_emb,
                char_start=char_offset,
                char_end=char_offset + chunk_chars,
            ))
            token_offset = end_token
            char_offset += chunk_chars + 1

        return result
