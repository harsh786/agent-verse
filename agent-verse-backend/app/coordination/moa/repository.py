"""Idempotent in-memory MoA metadata repository used by unit runtimes."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.coordination.moa.models import MoALayer, MoAProposal
from app.db.models.coordination import COORDINATION_TABLES
from app.db.rls import sqlalchemy_rls_context


class InMemoryMoARepository:
    def __init__(self) -> None:
        self._layers: dict[tuple[str, str, int], MoALayer] = {}
        self._proposals: dict[tuple[str, str, int, str, int], MoAProposal] = {}
        self._commands: dict[tuple[str, str], MoALayer | MoAProposal] = {}
        self._lock = asyncio.Lock()

    async def create_layer(self, layer: MoALayer) -> MoALayer:
        async with self._lock:
            command = (layer.tenant_id, layer.idempotency_key)
            prior = self._commands.get(command)
            if prior is not None:
                if not isinstance(prior, MoALayer):
                    raise ValueError("idempotency key reused across entity types")
                return prior
            key = (layer.tenant_id, layer.strategy_execution_id, layer.layer_index)
            if key in self._layers:
                raise ValueError("MoA layer already exists")
            self._layers[key] = layer
            self._commands[command] = layer
            return layer

    async def save_proposal(self, proposal: MoAProposal) -> MoAProposal:
        async with self._lock:
            command = (proposal.tenant_id, proposal.idempotency_key)
            prior = self._commands.get(command)
            if prior is not None:
                if not isinstance(prior, MoAProposal):
                    raise ValueError("idempotency key reused across entity types")
                return prior
            key = (
                proposal.tenant_id,
                proposal.strategy_execution_id,
                proposal.layer_index,
                proposal.participant_id,
                proposal.attempt,
            )
            if key in self._proposals:
                raise ValueError("MoA proposal already exists")
            self._proposals[key] = proposal
            self._commands[command] = proposal
            return proposal

    async def layers(self, tenant_id: str, session_id: str) -> tuple[MoALayer, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._layers.values()
                    if item.tenant_id == tenant_id and item.session_id == session_id
                ),
                key=lambda item: item.layer_index,
            )
        )

    async def proposals(
        self, tenant_id: str, execution_id: str, layer_index: int
    ) -> tuple[MoAProposal, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._proposals.values()
                    if item.tenant_id == tenant_id
                    and item.strategy_execution_id == execution_id
                    and item.layer_index == layer_index
                ),
                key=lambda item: (item.participant_id, item.attempt),
            )
        )


def _layer_from_row(row: Any) -> MoALayer:
    return MoALayer(
        layer_id=str(row.id),
        tenant_id=str(row.tenant_id),
        session_id=str(row.session_id),
        strategy_execution_id=str(row.strategy_execution_id),
        layer_index=int(row.layer_index),
        aggregator_deployment_id=str(row.aggregator_deployment_id),
        quorum=int(row.quorum),
        deployment_ids=tuple(row.deployment_ids or ()),
        aggregate_reference=row.aggregate_reference,
        idempotency_key=str(row.idempotency_key),
    )


def _proposal_from_row(row: Any) -> MoAProposal:
    return MoAProposal(
        proposal_id=str(row.id),
        tenant_id=str(row.tenant_id),
        session_id=str(row.session_id),
        strategy_execution_id=str(row.strategy_execution_id),
        layer_index=int(row.layer_index),
        participant_id=str(row.participant_id),
        provider_id=str(row.provider_id),
        model_family=str(row.model_family),
        deployment_id=str(row.deployment_id),
        region=str(row.region),
        failure_domain=str(row.failure_domain),
        proposal_reference=str(row.proposal_reference),
        safe_excerpt=str(row.safe_excerpt),
        evidence_references=tuple(row.evidence_references or ()),
        predecessor_proposal_ids=tuple(row.predecessor_proposal_ids or ()),
        valid=bool(row.valid),
        rejection_reason=row.rejection_reason,
        tokens=int(row.tokens),
        latency_ms=int(row.latency_ms),
        cost_usd=float(row.cost_usd),
        quality_score=int(row.quality_score),
        attempt=int(row.attempt),
        idempotency_key=str(row.idempotency_key),
    )


class PostgresMoARepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create_layer(self, layer: MoALayer) -> MoALayer:
        table = COORDINATION_TABLES["moa_layers"]
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, layer.tenant_id),
        ):
            prior = (
                await db.execute(
                    select(table).where(
                        table.c.session_id == layer.session_id,
                        table.c.idempotency_key == layer.idempotency_key,
                    )
                )
            ).mappings().one_or_none()
            if prior is not None:
                return _layer_from_row(prior)
            await db.execute(
                insert(table).values(
                    id=layer.layer_id,
                    tenant_id=layer.tenant_id,
                    session_id=layer.session_id,
                    strategy_execution_id=layer.strategy_execution_id,
                    layer_index=layer.layer_index,
                    aggregator_deployment_id=layer.aggregator_deployment_id,
                    deployment_ids=list(layer.deployment_ids),
                    quorum=layer.quorum,
                    aggregate_reference=layer.aggregate_reference,
                    explanation={},
                    idempotency_key=layer.idempotency_key,
                    version=1,
                )
            )
            return layer

    async def save_proposal(self, proposal: MoAProposal) -> MoAProposal:
        table = COORDINATION_TABLES["moa_proposals"]
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, proposal.tenant_id),
        ):
            prior = (
                await db.execute(
                    select(table).where(
                        table.c.strategy_execution_id == proposal.strategy_execution_id,
                        table.c.idempotency_key == proposal.idempotency_key,
                    )
                )
            ).mappings().one_or_none()
            if prior is not None:
                return _proposal_from_row(prior)
            await db.execute(
                insert(table).values(
                    id=proposal.proposal_id,
                    tenant_id=proposal.tenant_id,
                    session_id=proposal.session_id,
                    strategy_execution_id=proposal.strategy_execution_id,
                    layer_index=proposal.layer_index,
                    participant_id=proposal.participant_id,
                    provider_id=proposal.provider_id,
                    model_family=proposal.model_family,
                    deployment_id=proposal.deployment_id,
                    region=proposal.region,
                    failure_domain=proposal.failure_domain,
                    prompt_input_references=[],
                    proposal_reference=proposal.proposal_reference,
                    safe_excerpt=proposal.safe_excerpt,
                    evidence_references=list(proposal.evidence_references),
                    predecessor_proposal_ids=list(proposal.predecessor_proposal_ids),
                    valid=proposal.valid,
                    rejection_reason=proposal.rejection_reason,
                    tokens=proposal.tokens,
                    latency_ms=proposal.latency_ms,
                    cost_usd=proposal.cost_usd,
                    quality_score=proposal.quality_score,
                    attempt=proposal.attempt,
                    idempotency_key=proposal.idempotency_key,
                    version=1,
                )
            )
            return proposal

    async def layers(self, tenant_id: str, session_id: str) -> tuple[MoALayer, ...]:
        table = COORDINATION_TABLES["moa_layers"]
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            rows = (
                await db.execute(
                    select(table)
                    .where(table.c.session_id == session_id)
                    .order_by(table.c.layer_index)
                )
            ).mappings()
            return tuple(_layer_from_row(row) for row in rows)

    async def proposals(
        self, tenant_id: str, execution_id: str, layer_index: int
    ) -> tuple[MoAProposal, ...]:
        table = COORDINATION_TABLES["moa_proposals"]
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            rows = (
                await db.execute(
                    select(table)
                    .where(
                        table.c.strategy_execution_id == execution_id,
                        table.c.layer_index == layer_index,
                    )
                    .order_by(table.c.participant_id, table.c.attempt)
                )
            ).mappings()
            return tuple(_proposal_from_row(row) for row in rows)

__all__ = ["InMemoryMoARepository", "PostgresMoARepository"]
