"""Typed exact-token prompt budgeting with immutable safety blocks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PromptPartition = Literal[
    "instructions",
    "current_plan",
    "working_memory",
    "reflexion",
    "long_term_memory",
    "episodic_memory",
    "procedural_memory",
    "retrieval",
    "tool_schemas",
    "conversation",
    "reserved_output",
]


class PromptBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    block_id: str
    partition: PromptPartition
    content: str
    immutable: bool = False
    required_fact_ids: tuple[str, ...] = ()
    source_references: tuple[str, ...] = ()


class PromptBudgetResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    blocks: tuple[PromptBlock, ...]
    input_tokens: int = Field(ge=0)
    reserved_output_tokens: int = Field(ge=0)
    token_savings: int = Field(ge=0)
    compressed_block_ids: tuple[str, ...]


class PromptBudgetExceededError(ValueError):
    pass


class PromptBudget:
    def __init__(
        self,
        *,
        token_counter: Callable[[str], int],
        compressor: Callable[[PromptBlock], PromptBlock],
    ) -> None:
        self._count = token_counter
        self._compress = compressor

    def fit(
        self,
        blocks: tuple[PromptBlock, ...],
        *,
        maximum_tokens: int,
        reserved_output_tokens: int,
    ) -> PromptBudgetResult:
        if reserved_output_tokens < 0 or maximum_tokens <= reserved_output_tokens:
            raise PromptBudgetExceededError("invalid prompt budget")
        input_limit = maximum_tokens - reserved_output_tokens
        original = sum(self._count(item.content) for item in blocks)
        if original <= input_limit:
            return PromptBudgetResult(
                blocks=blocks,
                input_tokens=original,
                reserved_output_tokens=reserved_output_tokens,
                token_savings=0,
                compressed_block_ids=(),
            )
        fitted = list(blocks)
        compressed: list[str] = []
        eligible = {
            "working_memory",
            "reflexion",
            "long_term_memory",
            "episodic_memory",
            "retrieval",
            "conversation",
        }
        for index, block in sorted(
            enumerate(fitted), key=lambda pair: (-self._count(pair[1].content), pair[1].block_id)
        ):
            if block.immutable or block.partition not in eligible:
                continue
            candidate = self._compress(block)
            if not set(block.required_fact_ids) <= set(candidate.required_fact_ids):
                continue
            if not set(block.source_references) <= set(candidate.source_references):
                continue
            if self._count(candidate.content) >= self._count(block.content):
                continue
            fitted[index] = candidate
            compressed.append(block.block_id)
            if sum(self._count(item.content) for item in fitted) <= input_limit:
                break
        final = sum(self._count(item.content) for item in fitted)
        if final > input_limit:
            raise PromptBudgetExceededError("immutable or fidelity-safe content exceeds budget")
        return PromptBudgetResult(
            blocks=tuple(fitted),
            input_tokens=final,
            reserved_output_tokens=reserved_output_tokens,
            token_savings=original - final,
            compressed_block_ids=tuple(compressed),
        )


__all__ = [
    "PromptBlock",
    "PromptBudget",
    "PromptBudgetExceededError",
    "PromptBudgetResult",
    "PromptPartition",
]
