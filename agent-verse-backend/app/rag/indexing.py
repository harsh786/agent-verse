"""Ingestion-time construction of persisted strategy-specific RAG indexes."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from app.knowledge.chunker_v2 import build_parent_windows
from app.providers.base import CompletionRequest, EmbedRequest, Message
from app.rag.contracts import RAGStrategy
from app.tenancy.context import TenantContext

_SUPPORTED_INDEXING_STRATEGIES = frozenset({RAGStrategy.RAPTOR, RAGStrategy.AGENTIC_CHUNKING})
_RAPTOR_SUMMARY_SYSTEM = (
    "Summarize each input group independently. Preserve facts, entities, and relationships. "
    "Return a JSON array of summary strings in the same order as the input groups."
)
_PROPOSITION_SYSTEM = (
    "Extract standalone factual propositions from each input chunk. Return a JSON array "
    "whose item at each position is an array of proposition strings for that input chunk."
)
_MAX_COMPLETION_BATCH_SIZE = 64
_MAX_EMBEDDING_BATCH_SIZE = 256


class CompletionProvider(Protocol):
    async def complete(self, request: CompletionRequest) -> Any: ...


class EmbeddingProvider(Protocol):
    async def embed(self, request: EmbedRequest) -> Any: ...


@dataclass(frozen=True, slots=True)
class IndexingDependency:
    """LLM dependency resolved independently for one indexing strategy."""

    provider: CompletionProvider
    model: str

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("Indexing model cannot be empty")


@dataclass(frozen=True, slots=True)
class RAGIndexingConfig:
    """Validated ingestion-time strategy selection and index shape."""

    strategies: frozenset[RAGStrategy] = field(default_factory=frozenset)
    raptor_cluster_size: int = 4
    raptor_max_levels: int = 3
    parent_window_size: int = 1
    raptor_summary_batch_size: int = 16
    proposition_batch_size: int = 16
    embedding_batch_size: int = 64

    def __post_init__(self) -> None:
        unsupported = self.strategies - _SUPPORTED_INDEXING_STRATEGIES
        if unsupported:
            values = ", ".join(sorted(strategy.value for strategy in unsupported))
            raise ValueError(f"Unsupported ingestion-time RAG strategies: {values}")
        if self.raptor_cluster_size < 2:
            raise ValueError("raptor_cluster_size must be at least 2")
        if self.raptor_max_levels < 1:
            raise ValueError("raptor_max_levels must be positive")
        if self.parent_window_size < 0:
            raise ValueError("parent_window_size cannot be negative")
        if not 1 <= self.raptor_summary_batch_size <= _MAX_COMPLETION_BATCH_SIZE:
            raise ValueError("raptor_summary_batch_size is outside the supported range")
        if not 1 <= self.proposition_batch_size <= _MAX_COMPLETION_BATCH_SIZE:
            raise ValueError("proposition_batch_size is outside the supported range")
        if not 1 <= self.embedding_batch_size <= _MAX_EMBEDDING_BATCH_SIZE:
            raise ValueError("embedding_batch_size is outside the supported range")


@dataclass(frozen=True, slots=True)
class RAGIndexRecord:
    """One dimension-neutral record ready for canonical chunk persistence."""

    chunk_id: str
    document_id: str
    content: str
    embedding: list[float]
    chunk_index: int
    strategy: RAGStrategy
    metadata: dict[str, Any] = field(default_factory=dict)
    parent_chunk_id: str | None = None
    chunk_level: str = "leaf"
    window_start: int | None = None
    window_end: int | None = None
    window_id: str | None = None
    hierarchy_level: int = 0
    is_proposition: bool = False
    strategy_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def embedding_dimension(self) -> int:
        return len(self.embedding)

    def to_search_result(self, *, score: float) -> dict[str, Any]:
        metadata = {
            **self.metadata,
            **self.strategy_metadata,
            "strategy": self.strategy.value,
            "hierarchy_level": self.hierarchy_level,
            "is_proposition": self.is_proposition,
            "parent_chunk_id": self.parent_chunk_id,
            "window_id": self.window_id,
        }
        return {
            "chunk_id": self.chunk_id,
            "content": self.content,
            "score": score,
            "metadata": metadata,
            "citation_chunk_id": self.parent_chunk_id or self.chunk_id,
            "citation_content": self.content,
        }


class RAGIndexingPipeline:
    """Build and atomically persist configured strategy indexes for one document."""

    def __init__(
        self,
        *,
        store: Any,
        embedder: EmbeddingProvider,
        dependencies: Mapping[RAGStrategy, IndexingDependency],
        config: RAGIndexingConfig,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._dependencies = dict(dependencies)
        self._config = config
        missing = config.strategies - set(self._dependencies)
        if missing:
            values = ", ".join(sorted(strategy.value for strategy in missing))
            raise ValueError(f"Missing indexing dependencies for: {values}")

    @staticmethod
    def _stable_id(document_id: str, strategy: RAGStrategy, path: str) -> str:
        return uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"agentverse-index:{document_id}:{strategy.value}:{path}",
        ).hex

    async def index_document(
        self,
        *,
        collection_id: str,
        document_id: str,
        chunks: list[str],
        tenant_ctx: TenantContext,
        metadata: dict[str, Any] | None = None,
    ) -> list[RAGIndexRecord]:
        """Construct all configured indexes before making them query-visible."""
        leaf_contents = [content.strip() for content in chunks if content.strip()]
        if not leaf_contents:
            return []

        records: list[RAGIndexRecord] = []
        leaves: list[RAGIndexRecord] = []
        if RAGStrategy.RAPTOR in self._config.strategies:
            leaves = self._make_raptor_leaves(document_id, leaf_contents, metadata or {})
            records.extend(leaves)
            records = await self._add_raptor_hierarchy(records, leaves)

        if RAGStrategy.AGENTIC_CHUNKING in self._config.strategies:
            proposition_parents = self._make_parent_leaves(
                document_id,
                leaf_contents,
                metadata or {},
            )
            records.extend(proposition_parents)
            records.extend(
                await self._make_propositions(
                    document_id,
                    leaf_contents,
                    proposition_parents,
                    metadata or {},
                )
            )

        if not records:
            return []
        indexed_records = [
            replace(record, chunk_index=chunk_index) for chunk_index, record in enumerate(records)
        ]
        embedded = await self._embed_records(indexed_records)
        await self._store.persist_index_records(
            embedded,
            collection_id=collection_id,
            tenant_ctx=tenant_ctx,
        )
        return embedded

    def _make_raptor_leaves(
        self,
        document_id: str,
        chunks: list[str],
        metadata: dict[str, Any],
    ) -> list[RAGIndexRecord]:
        return [
            RAGIndexRecord(
                chunk_id=self._stable_id(
                    document_id,
                    RAGStrategy.RAPTOR,
                    f"leaf:{index}",
                ),
                document_id=document_id,
                content=content,
                embedding=[],
                chunk_index=index,
                strategy=RAGStrategy.RAPTOR,
                metadata=dict(metadata),
                window_start=max(0, index - self._config.parent_window_size),
                window_end=min(len(chunks), index + self._config.parent_window_size + 1),
                window_id=f"window-{document_id}-{index}",
                strategy_metadata={"node_type": "leaf"},
            )
            for index, content in enumerate(chunks)
        ]

    def _make_parent_leaves(
        self,
        document_id: str,
        chunks: list[str],
        metadata: dict[str, Any],
    ) -> list[RAGIndexRecord]:
        windows = build_parent_windows(chunks, self._config.parent_window_size)
        return [
            RAGIndexRecord(
                chunk_id=self._stable_id(
                    document_id,
                    RAGStrategy.AGENTIC_CHUNKING,
                    f"parent:{window.center_index}",
                ),
                document_id=document_id,
                content=window.content,
                embedding=[],
                chunk_index=window.center_index,
                strategy=RAGStrategy.AGENTIC_CHUNKING,
                metadata=dict(metadata),
                window_start=window.start_index,
                window_end=window.end_index,
                window_id=f"window-{document_id}-{window.center_index}",
                strategy_metadata={"node_type": "parent"},
            )
            for window in windows
        ]

    async def _add_raptor_hierarchy(
        self,
        records: list[RAGIndexRecord],
        leaves: list[RAGIndexRecord],
    ) -> list[RAGIndexRecord]:
        current = leaves
        for level in range(1, self._config.raptor_max_levels + 1):
            if len(current) <= 1:
                break
            groups = [
                current[index : index + self._config.raptor_cluster_size]
                for index in range(0, len(current), self._config.raptor_cluster_size)
            ]
            summaries = await self._complete_json_batches(
                RAGStrategy.RAPTOR,
                _RAPTOR_SUMMARY_SYSTEM,
                [[record.content for record in group] for group in groups],
                self._config.raptor_summary_batch_size,
            )
            if len(summaries) != len(groups) or not all(
                isinstance(summary, str) and summary.strip() for summary in summaries
            ):
                raise RuntimeError("RAPTOR provider returned an incomplete summary batch")

            parents: list[RAGIndexRecord] = []
            child_parent_ids: dict[str, str] = {}
            for group_index, (group, summary) in enumerate(zip(groups, summaries, strict=True)):
                parent_id = self._stable_id(
                    group[0].document_id,
                    RAGStrategy.RAPTOR,
                    f"summary:{level}:{group_index}",
                )
                for child in group:
                    child_parent_ids[child.chunk_id] = parent_id
                parents.append(
                    RAGIndexRecord(
                        chunk_id=parent_id,
                        document_id=group[0].document_id,
                        content=summary.strip(),
                        embedding=[],
                        chunk_index=group_index,
                        strategy=RAGStrategy.RAPTOR,
                        metadata=dict(group[0].metadata),
                        chunk_level="parent",
                        hierarchy_level=level,
                        strategy_metadata={
                            "node_type": "summary",
                            "child_chunk_ids": [child.chunk_id for child in group],
                        },
                    )
                )
            records = [
                replace(record, parent_chunk_id=child_parent_ids[record.chunk_id])
                if record.chunk_id in child_parent_ids
                else record
                for record in records
            ]
            records.extend(parents)
            current = parents
        return records

    async def _make_propositions(
        self,
        document_id: str,
        chunks: list[str],
        parents: list[RAGIndexRecord],
        metadata: dict[str, Any],
    ) -> list[RAGIndexRecord]:
        payload = await self._complete_json_batches(
            RAGStrategy.AGENTIC_CHUNKING,
            _PROPOSITION_SYSTEM,
            chunks,
            self._config.proposition_batch_size,
        )
        if len(payload) != len(chunks) or not all(isinstance(item, list) for item in payload):
            raise RuntimeError("Agentic chunking provider returned an incomplete batch")
        propositions: list[RAGIndexRecord] = []
        for parent_index, raw_propositions in enumerate(payload):
            parent = parents[parent_index]
            for proposition_index, proposition in enumerate(raw_propositions):
                if not isinstance(proposition, str) or not proposition.strip():
                    raise RuntimeError("Agentic chunking provider returned an invalid proposition")
                propositions.append(
                    RAGIndexRecord(
                        chunk_id=self._stable_id(
                            document_id,
                            RAGStrategy.AGENTIC_CHUNKING,
                            f"proposition:{parent_index}:{proposition_index}",
                        ),
                        document_id=document_id,
                        content=proposition.strip(),
                        embedding=[],
                        chunk_index=len(propositions),
                        strategy=RAGStrategy.AGENTIC_CHUNKING,
                        metadata=dict(metadata),
                        parent_chunk_id=parent.chunk_id,
                        chunk_level="child",
                        window_start=parent.window_start,
                        window_end=parent.window_end,
                        window_id=parent.window_id,
                        is_proposition=True,
                        strategy_metadata={
                            "node_type": "proposition",
                            "proposition_index": proposition_index,
                        },
                    )
                )
        return propositions

    async def _complete_json_batches(
        self,
        strategy: RAGStrategy,
        system: str,
        payload: list[Any],
        batch_size: int,
    ) -> list[Any]:
        completed: list[Any] = []
        for start in range(0, len(payload), batch_size):
            batch = payload[start : start + batch_size]
            parsed = await self._complete_json_batch(strategy, system, batch)
            if len(parsed) != len(batch):
                raise RuntimeError(f"{strategy.value} provider returned an incomplete batch")
            completed.extend(parsed)
        return completed

    async def _complete_json_batch(
        self,
        strategy: RAGStrategy,
        system: str,
        payload: list[Any],
    ) -> list[Any]:
        dependency = self._dependencies[strategy]
        response = await dependency.provider.complete(
            CompletionRequest(
                messages=[
                    Message(role="system", content=system),
                    Message(role="user", content=f"Batch:\n{json.dumps(payload)}"),
                ],
                model=dependency.model,
                max_tokens=2_000,
                temperature=0.0,
            )
        )
        try:
            parsed = json.loads(response.content)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Indexing provider returned invalid JSON") from exc
        if not isinstance(parsed, list):
            raise RuntimeError("Indexing provider batch response must be a JSON array")
        return parsed

    async def _embed_records(
        self,
        records: list[RAGIndexRecord],
    ) -> list[RAGIndexRecord]:
        embeddings: list[list[float]] = []
        for start in range(0, len(records), self._config.embedding_batch_size):
            batch = records[start : start + self._config.embedding_batch_size]
            response = await self._embedder.embed(
                EmbedRequest(texts=[record.content for record in batch])
            )
            if len(response.embeddings) != len(batch) or any(
                not embedding for embedding in response.embeddings
            ):
                raise RuntimeError("Embedding provider returned an incomplete indexing batch")
            embeddings.extend(response.embeddings)
        dimensions = {len(embedding) for embedding in embeddings}
        if len(dimensions) != 1:
            raise RuntimeError("Embedding provider returned mixed dimensions")
        return [
            replace(record, embedding=list(embedding))
            for record, embedding in zip(records, embeddings, strict=True)
        ]
