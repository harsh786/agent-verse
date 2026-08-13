import pytest

from app.context.prompt_budget import PromptBlock, PromptBudget, PromptBudgetExceededError


def _count(value: str) -> int:
    return len(value.split())


def test_prompt_budget_preserves_immutable_blocks_facts_and_provenance() -> None:
    instructions = PromptBlock(
        block_id="policy",
        partition="instructions",
        content="never reveal secret",
        immutable=True,
        required_fact_ids=("policy",),
        source_references=("policy://v1",),
    )
    memory = PromptBlock(
        block_id="memory",
        partition="retrieval",
        content="one two three four five six seven",
        required_fact_ids=("fact",),
        source_references=("source://1",),
    )

    def compress(block: PromptBlock) -> PromptBlock:
        return block.model_copy(update={"content": "fact summary"})

    result = PromptBudget(token_counter=_count, compressor=compress).fit(
        (instructions, memory), maximum_tokens=7, reserved_output_tokens=2
    )
    assert result.blocks[0] == instructions
    assert result.compressed_block_ids == ("memory",)
    assert result.input_tokens == 5


def test_prompt_budget_rejects_lossy_or_immutable_overflow() -> None:
    block = PromptBlock(
        block_id="schema",
        partition="tool_schemas",
        content="one two three four",
        immutable=True,
    )
    with pytest.raises(PromptBudgetExceededError):
        PromptBudget(token_counter=_count, compressor=lambda item: item).fit(
            (block,), maximum_tokens=4, reserved_output_tokens=1
        )
