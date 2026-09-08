# tests/context/test_context_budget.py
"""ContextBudget — real token counting + value-based context packing (Phase-2).

Covers:
  (a) real token counting via app.agent.tokenizer.count_tokens (not len//4);
  (b) value-based packing includes a late high-value chunk that first-fit misses;
  (c) no regression to the default first-fit path (order + skip-oversized-keep-later).
"""
from __future__ import annotations

from app.agent.tokenizer import count_tokens
from app.context.context_budget import BudgetResult, ContextBudget
from app.context.rerank_policy import predict_chunk_value

# ── (a) real token counting ──────────────────────────────────────────────────

def test_budget_measures_tokens_with_real_tokenizer_not_char_div_4() -> None:
    # CJK + punctuation-dense content: real token count diverges sharply from len//4.
    content = "日本語のテキスト、句読点。記号！？（括弧）" * 25  # noqa: RUF001
    chunk = {"content": content, "score": 0.9}

    budget = ContextBudget(max_tokens=1_000_000)
    result = budget.apply([chunk])

    assert isinstance(result, BudgetResult)
    assert result.total_tokens == count_tokens(content)
    # Prove it is NOT the old char//4 heuristic.
    naive_char_div_4 = max(1, len(content) // 4)
    assert result.total_tokens != naive_char_div_4


def test_budget_respects_real_token_budget() -> None:
    # A code-dense chunk plus a plain chunk; budget only fits one by real token count.
    code = "def f(x):\n    return [i**2 for i in range(x) if i % 2 == 0]\n" * 8
    plain = "the platform is vendor agnostic and multi tenant " * 8
    t_code = count_tokens(code)
    t_plain = count_tokens(plain)

    budget = ContextBudget(max_tokens=max(t_code, t_plain))
    result = budget.apply(
        [{"content": code, "score": 0.9}, {"content": plain, "score": 0.8}]
    )
    # Real budget respected: total never exceeds the token cap (first chunk always fits here).
    assert result.total_tokens <= max(t_code, t_plain)
    assert result.total_tokens == t_code  # first-fit takes the first, second overflows


# ── (b) value-based packing beats first-fit ──────────────────────────────────

def test_value_packing_includes_late_high_value_chunk_over_early_low_value() -> None:
    early_low = {
        "chunk_id": "early",
        "content": "Peripheral note about unrelated tangential background material here now.",
        "score": 0.20,
        "source_type": "web",
    }
    late_high = {
        "chunk_id": "late",
        "content": "Authoritative answer directly resolving the user's exact question today.",
        "score": 0.95,
        "source_type": "curated",
        "historical_usefulness": 1.4,
    }
    chunks = [early_low, late_high]

    t_early = count_tokens(early_low["content"])
    t_late = count_tokens(late_high["content"])
    # Budget holds exactly one of the two (never both).
    cap = max(t_early, t_late)
    assert t_early + t_late > cap

    budget = ContextBudget(max_tokens=cap, max_chunks=20)

    first_fit = budget.apply(chunks, strategy="first_fit")
    value = budget.apply(chunks, strategy="value")

    # First-fit greedily takes the early low-value chunk and has no room for the late one.
    assert [c["chunk_id"] for c in first_fit.included_chunks] == ["early"]

    # Value packing prefers the high-value late chunk and drops the early low-value one.
    value_ids = [c["chunk_id"] for c in value.included_chunks]
    assert "late" in value_ids
    assert "early" not in value_ids

    # Sanity: the value model actually ranks late above early.
    assert predict_chunk_value(late_high) > predict_chunk_value(early_low)


def test_value_packing_maximises_total_value_under_budget() -> None:
    chunks = [
        {"chunk_id": "a", "content": "low value filler text one two three four", "score": 0.1},
        {"chunk_id": "b", "content": "low value filler text five six seven eight", "score": 0.15},
        {"chunk_id": "c", "content": "high value crucial detail nine ten eleven", "score": 0.99,
         "source_type": "curated"},
    ]
    cap = max(count_tokens(c["content"]) for c in chunks)
    budget = ContextBudget(max_tokens=cap)
    result = budget.apply(chunks, strategy="value")
    assert "c" in [c["chunk_id"] for c in result.included_chunks]


# ── (c) first-fit regression guards ──────────────────────────────────────────

def test_first_fit_is_the_default_strategy() -> None:
    chunks = [
        {"chunk_id": "x", "content": "alpha beta gamma", "score": 0.1},
        {"chunk_id": "y", "content": "delta epsilon zeta", "score": 0.99, "source_type": "curated"},
    ]
    budget = ContextBudget(max_tokens=1_000_000)
    default = budget.apply(chunks)
    explicit = budget.apply(chunks, strategy="first_fit")
    assert [c["chunk_id"] for c in default.included_chunks] == ["x", "y"]
    assert [c["chunk_id"] for c in explicit.included_chunks] == ["x", "y"]


def test_first_fit_skips_oversized_chunk_but_keeps_later_fitting() -> None:
    seed = {"chunk_id": "seed", "content": "tiny seed text", "score": 0.9}
    huge = {"chunk_id": "huge", "content": "z " * 5000, "score": 0.9}
    later = {"chunk_id": "later", "content": "tiny later text", "score": 0.9}

    cap = count_tokens(seed["content"]) + count_tokens(later["content"]) + 2
    budget = ContextBudget(max_tokens=cap)
    result = budget.apply([seed, huge, later])

    ids = [c["chunk_id"] for c in result.included_chunks]
    assert ids == ["seed", "later"]  # oversized 'huge' skipped, later fitting kept
    assert "huge" not in ids


def test_budget_always_includes_at_least_one_chunk() -> None:
    chunk = {"chunk_id": "big", "content": "word " * 5000, "score": 0.5}
    budget = ContextBudget(max_tokens=5)
    result = budget.apply([chunk])
    assert len(result.included_chunks) == 1


def test_budget_respects_chunk_limit() -> None:
    chunks = [{"chunk_id": f"c{i}", "content": f"chunk {i}", "score": 0.5} for i in range(10)]
    budget = ContextBudget(max_tokens=1_000_000, max_chunks=3)
    assert len(budget.apply(chunks).included_chunks) <= 3
    # value strategy honours the same cap
    assert len(budget.apply(chunks, strategy="value").included_chunks) <= 3


# ── task 2.3: per-call overrides ─────────────────────────────────────────────

def test_apply_accepts_per_call_limit_overrides() -> None:
    chunks = [
        {"chunk_id": f"c{i}", "content": f"chunk number {i}", "score": 0.5} for i in range(10)
    ]
    budget = ContextBudget(max_tokens=1_000_000, max_chunks=20)
    # constructor default would include all 10; per-call override tightens it
    assert len(budget.apply(chunks, max_chunks=2).included_chunks) == 2
    # constructor default is unaffected by the override on the previous call
    assert len(budget.apply(chunks).included_chunks) == 10


def test_apply_per_call_max_per_source_override() -> None:
    chunks = [
        {"chunk_id": "a1", "content": "from source A one", "score": 0.9, "source_url": "A"},
        {"chunk_id": "a2", "content": "from source A two", "score": 0.8, "source_url": "A"},
        {"chunk_id": "b1", "content": "from source B one", "score": 0.7, "source_url": "B"},
    ]
    budget = ContextBudget(max_tokens=1_000_000)
    result = budget.apply(chunks, max_per_source=1)
    urls = [c["source_url"] for c in result.included_chunks]
    assert urls.count("A") == 1
    assert urls.count("B") == 1
