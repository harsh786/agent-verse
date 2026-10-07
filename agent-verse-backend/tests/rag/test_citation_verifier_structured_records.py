"""Answer grounding over flattened MongoDB records (MONGO-PIPELINE known answers).

Live finding (tests/real_world/test_mongo_pipeline_e2e.py::test_pipeline_known_answers):
retrieval was perfect (hit@1 = 1.0) but POST /rag/query refused Q05, Q11 and Q26 with
422 ``answer_ungrounded``. The documents here are the real planted records from
``tests/real_world/commerce_seed.py``, flattened and chunked by the platform's own
MongoDB flattener and chunker.

* Q05 (array): the bulk order's ``lines[6].title: Channapatna lacquer toy train`` starts
  at character 1203 of a 1248-character chunk; the synthesis context cut every citation
  at ~1195 characters, so the model never saw the answer (the live log shows the
  refusal right after synthesis, with no entailment call). An answer that quotes the
  key path (``lines[6]``) was also read as citing evidence 6 (out of range).
* Q26 (array): ``timeline[1].at`` in an answer was read as citation marker ``[1]`` and
  split the sentence into fragments the entailment model rejected.
* Q11 (multilingual): several markers in one sentence give the model bare fragments
  ("in Japanese yen"); it now gets the sentence, the record-format rules and the
  cross-language rule — the lexical identity check never decides a non-Latin claim.

The entailment model is a deterministic stand-in (no live LLM): it supports a claim
iff every scenario fact the claim mentions occurs in the evidence it was sent. That
checks what reaches the judge — an intact claim, the full cited record, the right
citation, the sentence — not the model's judgement.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any

import pytest

from app.ingestion.chunking_strategy_selector import ChunkingStrategySelector
from app.ingestion.connectors.mongodb_connector import _flatten_doc
from app.ingestion.content_classifier import ContentType
from app.providers.base import CompletionRequest, CompletionResponse
from app.rag.contracts import RAGCitation, RAGStrategy
from app.rag.gateway import ResolvedLLM
from app.rag_platform.retriever import (
    MinimalCitationVerifier,
    RAGRetriever,
    _excerpt,
    _query_terms,
    _synthesis_context_parts,
)
from app.tenancy.context import PlanTier, TenantContext
from tests.real_world import commerce_seed as cs

TENANT = TenantContext("tenant-structured", PlanTier.PROFESSIONAL, "key-structured")
TOY = "Channapatna lacquer toy train"


# ── The real planted records, flattened and chunked like the pipeline does ───


@pytest.fixture(scope="module")
def commerce() -> dict[str, Any]:
    data = cs.generate(300)
    questions = {q.id: q for q in cs.questions(data)}
    selector = ChunkingStrategySelector()

    def chunks(collection: str, key: str) -> list[str]:
        doc = data.find(collection, key)
        text = _flatten_doc({**doc, "_id": str(doc["_id"])})
        return selector.select_and_chunk(text, ContentType.TEXT)

    q05, q11, q26 = questions["Q05"], questions["Q11"], questions["Q26"]
    filler_orders = [chunks("orders", key)[0] for key in data.keys("orders")[10:14]]
    return {
        "questions": questions,
        "bulk": chunks(q05.collection, q05.key),
        "orders": filler_orders,
        "sakura": chunks(q11.collection, q11.key)[0],
        "meera": chunks("customers", "CUS-F0001")[0],
        "redis_pm": chunks(q26.collection, q26.key)[0],
        "webhook_pm": chunks("postmortems", "PM-2026-014")[0],
        "hindi_ticket": chunks("support_tickets", "100001")[0],
    }


def _citations(contents: Iterable[str]) -> list[RAGCitation]:
    return [
        RAGCitation(
            citation_id=f"citation-{index}",
            chunk_id=f"chunk-{index}",
            content=content,
            score=0.9,
            source="mongodb://commerce",
        )
        for index, content in enumerate(contents, start=1)
    ]


class _Judge:
    """Deterministic stand-in for the entailment model (see the module docstring)."""

    def __init__(self, facts: Iterable[str]) -> None:
        self.facts = [fact.casefold() for fact in facts]
        self.prompts: list[str] = []

    @staticmethod
    def parts(prompt: str) -> tuple[str, str, str]:
        head, evidence = prompt.split("\nEvidence:\n", 1)
        claim = head.split("\nClaim: ", 1)[1].split("\n", 1)[0]
        marker = "Sentence the claim was taken from (only to tell what the claim refers to): "
        sentence = head.split(marker, 1)[1].split("\n", 1)[0] if marker in head else ""
        return claim, sentence, evidence

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        prompt = str(request.messages[0].content)
        self.prompts.append(prompt)
        claim, sentence, evidence = self.parts(prompt)
        evidence_cf = evidence.casefold()
        asserted = [fact for fact in self.facts if fact in claim.casefold()]
        in_context = [fact for fact in self.facts if fact in sentence.casefold()]
        supported = bool(asserted) and all(fact in evidence_cf for fact in {*asserted, *in_context})
        verdict = (
            {"supported": True, "reason": "entailed"}
            if supported
            else {"supported": False, "reason": "not_entailed"}
        )
        return CompletionResponse(content=json.dumps(verdict), model="judge")


async def _verify(answer: str, contents: list[str], judge: _Judge | None) -> Any:
    verifier = MinimalCitationVerifier(provider=judge, model="judge" if judge else "")
    return await verifier.verify(answer, _citations(contents))


# ── Q05: the synthesis context must carry the array item ─────────────────────


def test_q05_old_head_cut_dropped_the_toy_train_and_the_excerpt_keeps_it(
    commerce: dict[str, Any],
) -> None:
    chunk = commerce["bulk"][0]
    question = commerce["questions"]["Q05"].question
    limit = 1200 - len("[1] ")  # 6000 chars / 5 citations, minus the marker
    assert len(chunk) > limit
    assert TOY in chunk
    assert TOY not in chunk[: limit - 1]  # what the model used to be shown

    excerpt = _excerpt(chunk, limit, _query_terms(question))

    assert len(excerpt) <= limit
    assert TOY in excerpt
    assert "order_no: ORD-770005" in excerpt  # the record's identity stays in view


@pytest.mark.asyncio
async def test_q05_synthesis_prompt_shows_the_toy_train_with_five_long_citations(
    commerce: dict[str, Any],
) -> None:
    long_chunks = commerce["bulk"][:5]
    assert all(len(chunk) > 1150 for chunk in long_chunks)
    requests: list[CompletionRequest] = []

    class _Writer:
        async def complete(self, request: CompletionRequest) -> CompletionResponse:
            requests.append(request)
            return CompletionResponse(content=f"The toy is the {TOY} [1].", model="m")

    provider = _Writer()

    async def resolve(_ctx: TenantContext, _strategy: RAGStrategy) -> ResolvedLLM:
        return ResolvedLLM(provider=provider, model="m")

    gateway = SimpleNamespace(dependencies=SimpleNamespace(llm_resolver=resolve))
    answer = await RAGRetriever(gateway=gateway).synthesize(
        query=commerce["questions"]["Q05"].question,
        tenant_ctx=TENANT,
        strategy=RAGStrategy.HYBRID,
        citations=_citations(long_chunks),
    )

    system = str(requests[0].messages[0].content)
    evidence = system.split("Evidence:\n", 1)[1]
    first = evidence.split("\n\n[2] ", 1)[0]
    assert TOY in first
    assert len(evidence) <= 6000 + 5 * 2
    assert answer.endswith("[1].")


def test_short_citations_leave_their_unused_share_to_a_long_one(
    commerce: dict[str, Any],
) -> None:
    contents = [commerce["bulk"][0], *commerce["orders"]]
    parts = _synthesis_context_parts(
        commerce["questions"]["Q05"].question, _citations(contents), 6000
    )
    assert len(parts) == 5
    assert parts[0] == f"[1] {contents[0]}"  # the whole chunk fits, nothing cut
    assert sum(len(part) + 2 for part in parts) <= 6000 + 2


def test_excerpt_without_query_terms_is_the_old_head_cut() -> None:
    content = "alpha " * 400
    assert _excerpt(content, 100, ["zzz"]) == content[:99].rstrip() + "…"


# ── Key paths in an answer are not citation markers ──────────────────────────


@pytest.mark.parametrize(
    ("answer", "claims"),
    [
        (
            f"The {TOY} (lines[6].title) is part of order ORD-770005 [1].",
            [(f"The {TOY} (lines[6].title) is part of order ORD-770005", [1])],
        ),
        (
            f"The Channapatna toy is the {TOY}, listed at lines[6] [1].",
            [(f"The Channapatna toy is the {TOY}, listed at lines[6]", [1])],
        ),
        (
            "The Redis primary failover completed at 02:14 (timeline[1].at) [1].",
            [("The Redis primary failover completed at 02:14 (timeline[1].at)", [1])],
        ),
        (
            "According to timeline[1], the Redis primary failover completed at 02:14 [1].",
            [("According to timeline[1], the Redis primary failover completed at 02:14", [1])],
        ),
        (
            "Its first item items[0].sku is SKU-BULK-001 [2].",
            [("Its first item items[0].sku is SKU-BULK-001", [2])],
        ),
        # A marker glued to a word or number is still a marker.
        (f"The toy is the {TOY}[1].", [(f"The toy is the {TOY}", [1])]),
        ("The failover completed at 02:14[1].", [("The failover completed at 02:14", [1])]),
    ],
)
def test_key_path_indexes_are_not_read_as_citations(
    commerce: dict[str, Any], answer: str, claims: list[tuple[str, list[int]]]
) -> None:
    evidence = "\n".join([commerce["bulk"][0], commerce["redis_pm"]])
    assert MinimalCitationVerifier._atomic_claims(answer, evidence) == claims


# ── Q05 / Q11 / Q26: correct answers are accepted ────────────────────────────


@pytest.mark.asyncio
async def test_q05_answer_quoting_the_key_path_is_grounded(commerce: dict[str, Any]) -> None:
    judge = _Judge([TOY, "ORD-770005", "Kondapalli wooden elephant"])
    result = await _verify(
        f"The Channapatna toy in order ORD-770005 is the {TOY} (lines[6].title) [1].",
        [commerce["bulk"][0], *commerce["orders"]],
        judge,
    )
    assert result.grounded, result
    assert len(judge.prompts) == 1
    claim, _sentence, evidence = _Judge.parts(judge.prompts[0])
    assert "lines[6].title" in claim
    # The judge sees the whole stored chunk, one field per line again.
    assert f"lines[6].title: {TOY}\n" in evidence


@pytest.mark.asyncio
async def test_q26_answer_quoting_the_timeline_path_is_grounded(
    commerce: dict[str, Any],
) -> None:
    judge = _Judge(["02:14", "Redis primary failover completed", "PM-2026-021"])
    result = await _verify(
        "During PM-2026-021 the Redis primary failover completed at 02:14 (timeline[1].at) [1].",
        [commerce["redis_pm"], commerce["webhook_pm"]],
        judge,
    )
    assert result.grounded, result
    assert len(judge.prompts) == 1  # one intact claim, not "(timeline" + "at)"
    prompt = judge.prompts[0]
    assert "'timeline[1].at' is the time of 'timeline[1].event'" in prompt


@pytest.mark.asyncio
async def test_q11_fragments_reach_the_judge_with_their_sentence(
    commerce: dict[str, Any],
) -> None:
    sakura = commerce["sakura"]
    judge = _Judge(["株式会社サクラ物流", "consolidated monthly invoicing", "Japanese yen", "pdf"])
    result = await _verify(
        "株式会社サクラ物流 prefers consolidated monthly invoicing [1], in Japanese yen [1] "
        "and as PDF [1].",
        [sakura],
        judge,
    )
    assert result.grounded, result
    assert len(judge.prompts) == 3
    sentences = [_Judge.parts(prompt)[1] for prompt in judge.prompts]
    assert all(s.startswith("株式会社サクラ物流 prefers consolidated") for s in sentences)
    assert "in another language or script" in judge.prompts[0]


@pytest.mark.asyncio
async def test_non_latin_claims_are_decided_by_the_judge_not_a_lexical_gate(
    commerce: dict[str, Any],
) -> None:
    ticket = commerce["hindi_ticket"]
    answer = "The customer accepted a ₹５００ voucher for the five-day Pune delay [1]."
    judge = _Judge(["₹500"])
    supported = await _verify(answer, [ticket], judge)
    assert supported.grounded
    assert len(judge.prompts) == 1
    assert "₹500" in _Judge.parts(judge.prompts[0])[0]  # NFKC: full-width digits

    without_model = await _verify(answer, [ticket], None)
    assert not without_model.grounded
    assert without_model.reason == "unsupported"


# ── Genuinely unsupported answers stay refused ───────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "answer", "contents", "facts"),
    [
        # wrong number
        (
            "Q26",
            "The Redis primary failover completed at 02:41 (timeline[1].at) [1].",
            ["redis_pm"],
            ["02:41", "02:14"],
        ),
        # wrong list item: item #131 is past the 100 indexed array items
        (
            "Q05",
            "The Channapatna toy in ORD-770005 is the Kondapalli wooden elephant "
            "(lines[130].title) [1].",
            ["bulk0"],
            [TOY, "Kondapalli wooden elephant"],
        ),
        # an answer from a different record: PM-2026-014 has no 02:14 failover
        (
            "Q26",
            "The Redis primary failover completed at 02:14 [2].",
            ["redis_pm", "webhook_pm"],
            ["02:14", "Redis primary failover completed"],
        ),
        # an answer from a different customer: Tamil invoices are Meera's (CUS-F0001)
        (
            "Q11",
            "株式会社サクラ物流 wants every invoice in Tamil [1].",
            ["sakura", "meera"],
            ["Tamil"],
        ),
    ],
)
async def test_unsupported_structured_answers_stay_refused(
    commerce: dict[str, Any],
    question: str,
    answer: str,
    contents: list[str],
    facts: list[str],
) -> None:
    lookup = {**commerce, "bulk0": commerce["bulk"][0]}
    docs = [lookup[name] for name in contents]
    judged = await _verify(answer, docs, _Judge(facts))
    assert not judged.grounded, question
    assert judged.reason == "unsupported"
    unjudged = await _verify(answer, docs, None)
    assert not unjudged.grounded


@pytest.mark.asyncio
async def test_abstention_without_a_marker_is_refused_without_a_judge_call(
    commerce: dict[str, Any],
) -> None:
    judge = _Judge(["PM-2026-099"])
    result = await _verify(
        "The information about incident PM-2026-099 is not available in the evidence.",
        [commerce["webhook_pm"], commerce["redis_pm"]],
        judge,
    )
    assert not result.grounded
    assert result.reason == "invalid_citation"
    assert judge.prompts == []


@pytest.mark.asyncio
async def test_out_of_range_citation_is_still_invalid(commerce: dict[str, Any]) -> None:
    judge = _Judge([TOY])
    result = await _verify(
        f"The toy is the {TOY} [6].", [commerce["bulk"][0], *commerce["orders"]], judge
    )
    assert not result.grounded
    assert result.reason == "invalid_citation"
    assert judge.prompts == []


# ── Number formatting in the verbatim check ──────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claim", "evidence", "grounded"),
    [
        ("The credit limit is 250,000.00 INR [1].", "The credit limit is 250000 INR", True),
        ("The credit limit is 2,50,000 INR [1].", "The credit limit is 250000.00 INR", True),
        ("The order total is 48,213.75 [1].", "The order total is 48213.750", True),
        ("The credit limit is 250,001 INR [1].", "The credit limit is 250000 INR", False),
        ("The order total is 48,213.57 [1].", "The order total is 48213.75", False),
        ("Version 3.5 is supported [1].", "Version 3.50.1 is supported", False),
    ],
)
async def test_verbatim_check_ignores_number_formatting_only(
    claim: str, evidence: str, grounded: bool
) -> None:
    result = await _verify(claim, [evidence], None)
    assert result.grounded is grounded
