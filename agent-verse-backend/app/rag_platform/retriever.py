"""Thin synthesis layer over the tenant-aware retrieval gateway."""

from __future__ import annotations

import inspect
import itertools
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Protocol, cast

from app.observability.logging import get_logger
from app.providers.base import CompletionRequest, Message
from app.rag.contracts import (
    RAGCitation,
    RAGExecutionResult,
    RAGStrategy,
    RAGStrategyTrace,
)
from app.rag.engine import RetrievalStrategyExecutionError
from app.rag.gateway import ResolvedLLM, RetrievalGateway
from app.tenancy.context import TenantContext

logger = get_logger(__name__)


class RAGSynthesisError(RuntimeError):
    """Raised when retrieved evidence cannot be synthesized safely."""


@dataclass(frozen=True, slots=True)
class CitationVerification:
    grounded: bool
    unsupported_claims: list[str]
    reason: str


class _BudgetContext(Protocol):
    @property
    def event_count(self) -> int: ...

    def wrap_provider(self, provider: Any, operation: str) -> Any: ...

    def traces(self, start: int = 0) -> list[RAGStrategyTrace]: ...


class MinimalCitationVerifier:
    """Verify citation-marker-scoped claims conservatively."""

    _MARKER_GROUP = re.compile(
        r"\[(?:\d+(?:\s*,\s*\d+)*)\]"
        r"(?:\s*(?:,\s*)?\[(?:\d+(?:\s*,\s*\d+)*)\])*"
    )

    # One provider call per claim: an answer with hundreds of cited sentences
    # would fan out into hundreds of paid calls. Claims past the cap are never
    # checked, so the answer is reported ungrounded rather than verified.
    MAX_ENTAILMENT_CALLS = 24

    def __init__(self, *, provider: Any = None, model: str = "") -> None:
        self.provider = provider
        self.model = model.strip()

    @staticmethod
    def _normalize(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text)
        normalized = re.sub(r"\[(?:\d+(?:\s*,\s*\d+)*)\]", "", normalized)
        normalized = " ".join(normalized.split()).strip()
        if not re.search(r"https?://\S+$", normalized, flags=re.IGNORECASE):
            normalized = normalized.rstrip(".!?").rstrip()
        return normalized

    @staticmethod
    def _key_path_masked(answer: str, evidence: str) -> str:
        """``answer`` with the array indexes of flattened-record key paths masked.

        A flattened MongoDB / JSON record renders arrays as ``lines[6].title`` and
        ``timeline[1].at``; an answer quoting such a path is not citing evidence
        6 or 1. ``lines[6]`` used to be read as a marker for citation 6 (out of
        range with top_k=5, so the whole answer was refused as
        ``invalid_citation``) and ``timeline[1]`` split one sentence into
        fragments ("... (timeline" / "at)") the entailment model rejected.

        An index counts as a key path when it is attached to an identifier and
        the path continues (``[6].title``, ``[0][1]``) or the attached token
        (``timeline[1]``) occurs verbatim in the cited evidence. Ambiguity only
        ever turns a marker into text, which leaves the claim uncited (refused),
        never the reverse. Same-length replacement keeps offsets aligned.
        """

        def replace(match: re.Match[str]) -> str:
            start = match.start()
            while start > 0 and (answer[start - 1].isalnum() or answer[start - 1] in "_.[]"):
                start -= 1
            token = answer[start : match.end()].lstrip(".")
            follows = answer[match.end() : match.end() + 2]
            continues = bool(re.match(r"\.[^\W\d]|\[\d", follows))
            if continues or (token and token in evidence):
                return f"{_KEY_OPEN}{match.group(1)}{_KEY_CLOSE}"
            return match.group()

        return _KEY_PATH_INDEX.sub(replace, answer)

    @staticmethod
    def _unmask(text: str) -> str:
        return text.replace(_KEY_OPEN, "[").replace(_KEY_CLOSE, "]")

    @classmethod
    def _claim_units(cls, answer: str, evidence: str = "") -> list[tuple[str, list[int], str]]:
        """Marker-scoped claims as ``(claim, references, sentence)``.

        ``sentence`` is the answer sentence the claim was cut from (markers
        removed). A sentence with several markers yields fragments such as
        "and PDF format"; the entailment model gets the sentence so it can tell
        what a fragment refers to.
        """
        masked = cls._key_path_masked(answer, evidence)
        boundaries = [0]
        boundaries.extend(
            m.end() for m in re.finditer(r"(?<=[.!?\u3002\uff01\uff1f])\s+|\n+", masked)
        )
        boundaries.append(len(masked))

        def sentence_at(position: int) -> str:
            for left, right in itertools.pairwise(boundaries):
                if left <= position < right:
                    sentence = cls._normalize(masked[left:right])
                    return cls._unmask(re.sub(r"\s+(?=[,;:.!?])", "", sentence))
            return ""

        atomic: list[tuple[str, list[int], str]] = []
        cursor = 0
        for marker in cls._MARKER_GROUP.finditer(masked):
            scoped = masked[cursor : marker.start()]
            scoped = re.sub(
                r"^[\s,;:.!?]+(?:and\s+|but\s+)?",
                "",
                scoped,
                flags=re.IGNORECASE,
            ).strip()
            scoped = re.sub(
                r"^(?:and|but)\s+",
                "",
                scoped,
                flags=re.IGNORECASE,
            )
            references = [int(value) for value in re.findall(r"\d+", marker.group())]
            if cls._normalize(scoped):
                anchor = marker.start()
                while anchor > cursor and masked[anchor - 1].isspace():
                    anchor -= 1
                atomic.append(
                    (cls._unmask(scoped), list(references), sentence_at(max(0, anchor - 1)))
                )
            cursor = marker.end()
        trailing = re.sub(r"^[\s,;:.!?]+", "", masked[cursor:]).strip()
        if cls._normalize(trailing):
            atomic.append((cls._unmask(trailing), [], cls._unmask(cls._normalize(trailing))))
        return atomic

    @classmethod
    def _atomic_claims(cls, answer: str, evidence: str = "") -> list[tuple[str, list[int]]]:
        return [(claim, refs) for claim, refs, _ in cls._claim_units(answer, evidence)]

    @classmethod
    def _identical(cls, claim: str, evidence: str) -> bool:
        """Claim and evidence are the same text up to NFKC, whitespace, a final
        full stop and number formatting (``1,234.50`` == ``1234.5``)."""
        return _canonical_numbers(cls._normalize(claim)) == _canonical_numbers(
            cls._normalize(evidence)
        )

    async def _provider_entails(
        self, claim: str, evidence: str, sentence: str = ""
    ) -> CitationVerification:
        if self.provider is None or not self.model:
            return CitationVerification(False, [claim], "unsupported")
        schema = {
            "type": "object",
            "properties": {
                "supported": {"type": "boolean"},
                "reason": {
                    "type": "string",
                    "enum": ["entailed", "not_entailed"],
                },
            },
            "required": ["supported", "reason"],
            "additionalProperties": False,
        }
        try:
            from app.providers.guarded_completion import complete_decision

            response = await complete_decision(
                self.provider,
                CompletionRequest(
                    messages=[
                        Message(
                            role="user",
                            content=_entailment_prompt(claim, evidence, sentence),
                        )
                    ],
                    model=self.model,
                    # Reasoning models (nemotron-3, Qwen3 with thinking) spend
                    # tokens before the JSON; 100 truncated them mid-thought.
                    max_tokens=1024,
                    response_schema=schema,
                ),
                role="rag_citation_verify",
            )
            parsed = _parse_json_object(str(response.content))
            if (
                not isinstance(parsed, dict)
                or set(parsed) != {"supported", "reason"}
                or not isinstance(parsed["supported"], bool)
                or parsed["reason"] not in {"entailed", "not_entailed"}
                or (parsed["supported"] is True and parsed["reason"] != "entailed")
                or (parsed["supported"] is False and parsed["reason"] != "not_entailed")
            ):
                raise ValueError("Invalid entailment response")
        except RetrievalStrategyExecutionError:
            raise
        except Exception as exc:
            logger.warning(
                "citation_entailment_failed",
                model=self.model,
                error_type=type(exc).__name__,
                error=str(exc)[:500],
                raw=str(locals().get("response") and response.content)[:300],
            )
            return CitationVerification(False, [claim], "verifier_failure")
        return CitationVerification(
            bool(parsed["supported"]),
            [] if parsed["supported"] else [claim],
            "supported" if parsed["supported"] else "unsupported",
        )

    async def verify(
        self,
        answer: str,
        citations: list[RAGCitation],
    ) -> CitationVerification:
        unsupported: list[str] = []
        reasons: list[str] = []
        checked = 0
        entailment_calls = 0
        all_evidence = "\n".join(citation.content for citation in citations)
        for claim, references, sentence in self._claim_units(answer, all_evidence):
            claim_normalized = self._normalize(claim)
            if not claim_normalized:
                continue
            checked += 1
            if not references or any(
                reference < 1 or reference > len(citations) for reference in references
            ):
                unsupported.append(claim)
                reasons.append("invalid_citation")
                continue
            evidence = " ".join(citations[index - 1].content for index in references)
            if self._identical(claim, evidence):
                continue
            if entailment_calls >= self.MAX_ENTAILMENT_CALLS:
                unsupported.append(claim)
                reasons.append("verification_limit_exceeded")
                continue
            entailment_calls += 1
            entailment = await self._provider_entails(
                claim,
                evidence,
                sentence if self._normalize(sentence) != claim_normalized else "",
            )
            if not entailment.grounded:
                unsupported.extend(entailment.unsupported_claims)
                reasons.append(entailment.reason)
        reason = (
            "supported"
            if checked > 0 and not unsupported
            else "contradiction"
            if "contradiction" in reasons
            else "invalid_citation"
            if "invalid_citation" in reasons
            else "verifier_failure"
            if "verifier_failure" in reasons
            else "verification_limit_exceeded"
            if "verification_limit_exceeded" in reasons
            else "unsupported"
        )
        return CitationVerification(
            grounded=checked > 0 and not unsupported,
            unsupported_claims=(
                unsupported if unsupported else [] if checked > 0 else ["No claims verified"]
            ),
            reason=reason,
        )


class RAGRetriever:
    """Retrieve through one gateway, then optionally synthesize its citations."""

    def __init__(
        self,
        *,
        gateway: RetrievalGateway | Any | None = None,
        citation_verifier: Any | None = None,
    ) -> None:
        self._gateway = gateway
        self._citation_verifier = citation_verifier or MinimalCitationVerifier()

    def set_gateway(self, gateway: RetrievalGateway | Any) -> None:
        """Set the injected gateway for application assembly and tests."""

        self._gateway = gateway

    async def retrieve(
        self,
        query: str,
        tenant_ctx: TenantContext,
        collection_id: str | None = None,
        strategy: str | RAGStrategy = RAGStrategy.HYBRID,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        execution_id: str = "",
        *,
        synthesize: bool = True,
        max_context_chars: int = 6000,
    ) -> RAGExecutionResult:
        """Execute canonical retrieval without alternate or fallback algorithms."""

        if self._gateway is None:
            raise RuntimeError("Retrieval gateway is not configured")
        if not collection_id:
            raise ValueError("collection_id is required")

        execute_kwargs: dict[str, Any] = {
            "collection_id": collection_id,
            "query": query,
            "strategy_id": strategy,
            "top_k": top_k,
            "filters": filters or {},
        }
        if execution_id:
            execute_kwargs["execution_id"] = execution_id
        result = await self._gateway.execute(tenant_ctx, **execute_kwargs)
        if not synthesize:
            return result
        budget_context = cast(_BudgetContext | None, result._budget_context)
        post_retrieval_cost_start = budget_context.event_count if budget_context is not None else 0
        answer = result.answer
        if not answer and result.resolved_strategy_id is RAGStrategy.RAFT:
            # RAFT answers come only from the deployed fine-tuned model; never
            # let the base model synthesize an answer reported as RAFT.
            raise RAGSynthesisError("RAFT result has no fine-tuned model answer")
        strategy_answered = bool(answer)
        if not answer and result.citations:
            answer = await self.synthesize(
                query=query,
                tenant_ctx=tenant_ctx,
                strategy=result.resolved_strategy_id,
                citations=result.citations,
                max_context_chars=max_context_chars,
                budget_context=budget_context,
            )
        result = result.model_copy(update={"answer": answer})
        result._budget_context = budget_context
        verified = await self.verify_result(
            result,
            tenant_ctx=tenant_ctx,
            cost_trace_start=post_retrieval_cost_start,
        )
        if (
            verified.grounded
            or not strategy_answered
            or not result.citations
            or result.resolved_strategy_id is RAGStrategy.RAFT
        ):
            return verified
        # A strategy wrote its own answer (agentic, self-RAG, ...) and it did not
        # pass citation verification, typically because it carries no [N]
        # markers. Re-synthesize from the same citations with the
        # citation-requiring synthesis (/knowledge/chat's path) and verify again;
        # the caller still sees grounded=False if that fails too.
        original_reason = next(
            (
                str(trace.detail.get("reason", "unsupported"))
                for trace in reversed(verified.strategy_trace)
                if trace.action == "citation_verification"
            ),
            "unsupported",
        )
        resynthesis_cost_start = budget_context.event_count if budget_context is not None else 0
        resynthesized = await self.synthesize(
            query=query,
            tenant_ctx=tenant_ctx,
            strategy=result.resolved_strategy_id,
            citations=result.citations,
            max_context_chars=max_context_chars,
            budget_context=budget_context,
        )
        retry = verified.model_copy(
            update={
                "answer": resynthesized,
                "strategy_trace": [
                    *verified.strategy_trace,
                    RAGStrategyTrace(
                        strategy=result.resolved_strategy_id,
                        action="answer_resynthesized",
                        status="complete",
                        detail={"original_reason": original_reason},
                    ),
                ],
            }
        )
        retry._budget_context = budget_context
        return await self.verify_result(
            retry,
            tenant_ctx=tenant_ctx,
            cost_trace_start=resynthesis_cost_start,
        )

    async def verify_result(
        self,
        result: RAGExecutionResult,
        *,
        tenant_ctx: TenantContext,
        cost_trace_start: int = 0,
    ) -> RAGExecutionResult:
        """Apply the same typed citation verification to any synthesized result."""

        trace = list(result.strategy_trace)
        try:
            verifier = self._citation_verifier
            if isinstance(verifier, MinimalCitationVerifier):
                provider = verifier.provider
                model = verifier.model
                if provider is None:
                    try:
                        resolved = await self._resolve_llm(
                            tenant_ctx,
                            result.resolved_strategy_id,
                        )
                        provider = resolved.provider
                        model = resolved.model
                    except RAGSynthesisError:
                        pass
                budget_context = cast(_BudgetContext | None, result._budget_context)
                if provider is not None and budget_context is not None:
                    provider = budget_context.wrap_provider(
                        provider,
                        "citation_verification",
                    )
                verifier = MinimalCitationVerifier(provider=provider, model=model)
            verification = await verifier.verify(
                result.answer,
                result.citations,
            )
            trace.append(
                RAGStrategyTrace(
                    strategy=result.resolved_strategy_id,
                    action="citation_verification",
                    status="complete",
                    detail={
                        "unsupported_claims": list(verification.unsupported_claims),
                        "reason": str(getattr(verification, "reason", "unsupported")),
                    },
                )
            )
            grounded = bool(verification.grounded)
        except RetrievalStrategyExecutionError:
            raise
        except Exception:
            trace.append(
                RAGStrategyTrace(
                    strategy=result.resolved_strategy_id,
                    action="citation_verification",
                    status="failed",
                    detail={"reason": "citation_verifier_unavailable"},
                )
            )
            grounded = False
        budget_context = cast(_BudgetContext | None, result._budget_context)
        if budget_context is not None:
            trace.extend(budget_context.traces(cost_trace_start))
        verified_result = result.model_copy(
            update={
                "answer": result.answer,
                "grounded": grounded,
                "strategy_trace": trace,
            }
        )
        verified_result._budget_context = budget_context
        return verified_result

    async def synthesize(
        self,
        *,
        query: str,
        tenant_ctx: TenantContext,
        strategy: RAGStrategy,
        citations: list[RAGCitation],
        max_context_chars: int = 6000,
        budget_context: _BudgetContext | None = None,
    ) -> str:
        """Synthesize canonical citations with the tenant's configured provider/model."""

        resolved = await self._resolve_llm(tenant_ctx, strategy)
        provider: Any = resolved.provider
        if provider is None:
            raise RAGSynthesisError("Tenant LLM provider is unavailable")
        if budget_context is not None:
            provider = budget_context.wrap_provider(provider, "synthesis")
        context_parts = _synthesis_context_parts(query, citations, max_context_chars)
        if not context_parts:
            raise RAGSynthesisError("No retrieved evidence fits the synthesis context")
        context = "\n\n".join(context_parts)

        try:
            from app.providers.guarded_completion import (
                complete_decision,
                generation_timeout_seconds,
            )

            response = await complete_decision(
                provider,
                CompletionRequest(
                    messages=[
                        Message(
                            role="system",
                            content=(
                                # The citation verifier checks every sentence
                                # against the evidence its marker names, so the
                                # format is a contract, not a style hint: the
                                # vaguer "cite with [N]" produced markers placed
                                # before quotes and uncited "Supporting evidence"
                                # sections, and correct answers were rejected.
                                "Answer using only the supplied evidence.\n"
                                "Citation rules:\n"
                                "- Write short sentences. End EVERY sentence that states a "
                                "fact with the number of the evidence that supports it, "
                                "e.g. 'Refunds take 7 business days [2].' Use [1][3] when a "
                                "sentence needs two pieces of evidence.\n"
                                "- Put the marker at the end of the sentence, before the "
                                "full stop's line break; never before a quote.\n"
                                "- Do not add a separate 'Sources' or 'Supporting evidence' "
                                "section and do not quote the evidence.\n"
                                "- State facts as the evidence gives them: do not add dates, "
                                "time zones, units or other details it does not state, and "
                                "do not copy record field paths such as lines[6].title; say "
                                "what the field holds instead.\n"
                                "- If the evidence does not answer the question, say that the "
                                "information is not available, without a marker.\n\n"
                                f"Evidence:\n{context}"
                            ),
                        ),
                        Message(role="user", content=query),
                    ],
                    model=resolved.model,
                    max_tokens=1200,
                ),
                role="rag_synthesis",
                tenant_ctx=tenant_ctx,
                timeout_seconds=generation_timeout_seconds(),
            )
        except Exception as exc:
            if isinstance(exc, RetrievalStrategyExecutionError):
                raise
            raise RAGSynthesisError("Answer synthesis failed") from exc
        answer = str(response.content).strip()
        if not answer:
            raise RAGSynthesisError("Answer synthesis returned no content")
        return answer

    async def _resolve_llm(
        self,
        tenant_ctx: TenantContext,
        strategy: RAGStrategy,
    ) -> ResolvedLLM:
        dependencies = getattr(self._gateway, "dependencies", None)
        resolver = getattr(dependencies, "llm_resolver", None)
        if resolver is None:
            raise RAGSynthesisError("Tenant LLM provider is unavailable")
        try:
            resolved = resolver(tenant_ctx, strategy)
            if inspect.isawaitable(resolved):
                resolved = await resolved
        except Exception as exc:
            raise RAGSynthesisError("Tenant LLM provider is unavailable") from exc
        if not isinstance(resolved, ResolvedLLM) or not resolved.model.strip():
            raise RAGSynthesisError("Tenant LLM provider is unavailable")
        provider = resolved.provider
        if provider is None:
            raise RAGSynthesisError("Tenant LLM provider is unavailable")
        return resolved


def citation_verification_detail(trace: list[RAGStrategyTrace]) -> dict[str, Any]:
    """The detail of the last ``citation_verification`` step of ``trace``.

    The refusal used to read ``strategy_trace[-1]``, but budget / cost steps are
    appended after verification, so every 422 said ``reason: unsupported`` (the
    default) and the log said ``reason=None`` — whatever really failed.
    """
    for step in reversed(trace):
        if step.action == "citation_verification":
            return dict(step.detail)
    # A verifier that names its step differently: its last step with a reason.
    for step in reversed(trace):
        if "reason" in step.detail:
            return dict(step.detail)
    return {}


# Kept for callers that configure a process-local singleton explicitly.
rag_retriever = RAGRetriever()


# Synthesis context: below this share an excerpt is too short to carry a fact.
_MIN_CITATION_SHARE = 600
_EXCERPT_HEAD = 240  # a record's identity lines (_id, order_no, name) stay in view
_ELLIPSIS = " … "
_STOPWORDS_TEXT = (
    "a an and are as at be by did do does for from had has have how in into is it its "
    "of on or that the their this to was were what when where which who whom whose why "
    "with during part"
)
_QUERY_STOPWORDS = frozenset(_STOPWORDS_TEXT.split())


def _query_terms(query: str) -> list[str]:
    """Distinct lower-cased query terms worth locating in evidence.

    Word-like tokens keep their internal ``-``/``:``/``.`` (``ord-770005``,
    ``02:14``) and also contribute their parts; a run of CJK / kana characters,
    which has no spaces, contributes its character bigrams.
    """
    text = unicodedata.normalize("NFKC", query).casefold()
    terms: dict[str, None] = {}
    for token in re.findall(r"[^\W_]+(?:[-:./][^\W_]+)*", text):
        pieces = [token, *re.split(r"[-:./]", token)]
        for piece in pieces:
            if re.fullmatch(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]+", piece):
                terms.update((piece[i : i + 2], None) for i in range(len(piece) - 1))
            elif len(piece) >= 3 and piece not in _QUERY_STOPWORDS:
                terms[piece] = None
    return list(terms)


def _excerpt(content: str, limit: int, terms: list[str]) -> str:
    """At most ``limit`` characters of ``content``, keeping what the query asks for.

    A head cut used to drop the answer whenever it sat past the cut — the bulk
    order's ``lines[6].title: Channapatna lacquer toy train`` started at
    character 1203 of a 1248-character chunk and the cut was at 1195, so the
    model never saw it and the question was refused. The excerpt keeps the
    record's head (its identity fields) plus the window with the most query
    terms, rarer terms weighing more; with no term in the content it is the
    old head cut.
    """
    if len(content) <= limit:
        return content
    if limit <= 1:
        return ""
    occurrences: list[tuple[int, int, str]] = []
    for term in terms:
        occurrences.extend(
            (m.start(), m.end(), term)
            for m in re.finditer(re.escape(term), content, flags=re.IGNORECASE)
        )
    head_len = min(_EXCERPT_HEAD, limit // 4)
    window_len = limit - head_len - len(_ELLIPSIS) - 1
    if not occurrences or window_len <= 0:
        return content[: limit - 1].rstrip() + "…"
    counts: dict[str, int] = {}
    for _, _, term in occurrences:
        counts[term] = counts.get(term, 0) + 1
    in_head = {term for start, end, term in occurrences if end <= head_len}

    def score(window_start: int) -> float:
        covered = set(in_head)
        covered.update(
            term
            for start, end, term in occurrences
            if start >= window_start and end <= window_start + window_len
        )
        return sum(1.0 / counts[term] for term in covered)

    candidates = {head_len}
    candidates.update(
        max(head_len, min(start - window_len // 3, len(content) - window_len))
        for start, _, _ in occurrences
    )
    best = min(candidates, key=lambda start: (-score(start), start))
    if best <= head_len:
        return content[: limit - 1].rstrip() + "…"
    window_start = best
    space = content.find(" ", window_start, window_start + 40)
    if space != -1:
        window_start = space + 1
    window = content[window_start : best + window_len]
    tail = "…" if best + window_len < len(content) else ""
    return content[:head_len].rstrip() + _ELLIPSIS + window.strip() + tail


def _synthesis_context_parts(
    query: str, citations: list[RAGCitation], max_context_chars: int
) -> list[str]:
    """``[N] evidence`` parts within ``max_context_chars``, numbered like ``citations``.

    Short citations take only what they need and the rest of the budget goes to
    the long ones (it used to be a fixed share each, so four short hits left
    most of the budget unused while the long one was cut). An oversized one is
    excerpted around the query terms (:func:`_excerpt`); the verifier still
    checks claims against the full stored text.
    """
    terms = _query_terms(query)
    sizes = [len(f"[{index}] ") + len(c.content) for index, c in enumerate(citations, start=1)]
    allocation = [0] * len(citations)
    remaining = max_context_chars
    order = sorted(range(len(citations)), key=lambda i: sizes[i])
    for position, index in enumerate(order):
        share = max(_MIN_CITATION_SHARE, remaining // max(1, len(citations) - position))
        allocation[index] = min(sizes[index], share, max(0, remaining))
        remaining -= allocation[index] + 2
    parts: list[str] = []
    used = 0
    for index, citation in enumerate(citations, start=1):
        if max_context_chars - used <= 80:
            break
        prefix = f"[{index}] "
        limit = min(allocation[index - 1], max_context_chars - used) - len(prefix)
        if limit <= 0:
            continue
        part = prefix + _excerpt(citation.content, limit, terms)
        parts.append(part)
        used += len(part) + 2
    return parts


# Masked brackets of a key-path array index (``lines[6]``) — see
# ``MinimalCitationVerifier._key_path_masked``. Same length as ``[`` / ``]``.
_KEY_OPEN, _KEY_CLOSE = "\u27e6", "\u27e7"
_KEY_PATH_INDEX = re.compile(r"(?<=[^\W\d]|[_\]])\[(\d+)\]")

# A number with optional thousands (``1,234``) or Indian (``2,50,000``) grouping
# and an optional decimal part.
_NUMBER = re.compile(
    r"(?<![\w.,])\d{1,3}(?:,\d{2})*,\d{3}(?:\.\d+)?(?![\w,])"
    r"|(?<![\w.,])\d+\.\d+(?![\w.])"
)


def _canonical_numbers(text: str) -> str:
    """``text`` with grouped / zero-padded decimals in one canonical form.

    ``1,234.50`` → ``1234.5``; ``2,50,000`` → ``250000``; ``99.00`` → ``99``.
    Only digit groups change — ``3.5`` and ``3-5`` stay different.
    """

    def canonical(match: re.Match[str]) -> str:
        value = match.group().replace(",", "")
        if "." in value:
            value = value.rstrip("0").rstrip(".")
        return value

    return _NUMBER.sub(canonical, text)


# ``path: value`` keys of a flattened record (``lines[6].title: …``). A chunker
# that joins lines with spaces leaves ``… lines[6].sku: SKU-7 lines[6].title: …``
# on one line; the entailment prompt gets one field per line again.
_FLAT_KEY = re.compile(r"\s+(?=[^\W\d][\w-]*(?:\[\d+\])*(?:\.[^\W\d][\w-]*(?:\[\d+\])*)*:\s)")
_STRUCTURED_KEY = re.compile(r"[^\W\d][\w-]*(?:\[\d+\]|\.[^\W\d][\w-]*)+:\s")


def _readable_evidence(evidence: str) -> str:
    text = unicodedata.normalize("NFKC", evidence)
    if len(_STRUCTURED_KEY.findall(text)) >= 3:
        text = _FLAT_KEY.sub("\n", text)
    return text


def _entailment_prompt(claim: str, evidence: str, sentence: str = "") -> str:
    """The single entailment question for one claim (structured-data aware).

    The judge decides alone whenever the claim is not the evidence verbatim —
    including any claim in a non-Latin script, which no lexical rule can
    compare to a translated or transliterated answer. It is told how flattened
    records read (``timeline[1].at`` is the time of ``timeline[1].event``) and
    which surface differences do not matter, and what still makes a claim
    unsupported.
    """
    context = (
        f"Sentence the claim was taken from (only to tell what the claim refers to): "
        f"{unicodedata.normalize('NFKC', sentence)}\n"
        if sentence
        else ""
    )
    return (
        "Determine whether the claim is fully entailed by the evidence. "
        'Reply with only this JSON object: {"supported": true, '
        '"reason": "entailed"} or {"supported": false, '
        '"reason": "not_entailed"}.\n'
        "How to judge:\n"
        "- Entailed: the evidence states the claim, possibly in other words. Not entailed: "
        "the claim contradicts the evidence, or adds a fact, number, date, time zone, unit, "
        "entity or qualifier the evidence does not state.\n"
        "- The evidence may be a flattened database record, one 'path: value' field per "
        "line. 'name[i]' is item i of a list, and fields that share the same 'name[i]' "
        "prefix belong to the same item (for example 'timeline[1].at' is the time of "
        "'timeline[1].event'). A claim that restates a field value or a note of the record "
        "is entailed.\n"
        "- These differences do not matter: number formatting (1,234.50 is 1234.5), "
        "currency symbols or codes (Rs, INR, \u20b9), date and time notation, letter width, "
        "and a name or value written in another language or script, translated or "
        "transliterated, when it clearly denotes the same thing.\n\n"
        f"Claim: {unicodedata.normalize('NFKC', claim)}\n"
        f"{context}"
        f"Evidence:\n{_readable_evidence(evidence)}"
    )


def _parse_json_object(text: str) -> Any:
    """The reply as strict JSON, after dropping ``<think>…</think>`` blocks.

    Some reasoning models inline their thought in tags even in JSON mode. Any
    other prose around the object is still rejected (it could carry a
    different, contradicting verdict).
    """
    return json.loads(re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip())
