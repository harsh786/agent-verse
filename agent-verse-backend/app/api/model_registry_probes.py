"""Capability probes for ``POST /models/configured/test-endpoint``.

Test connection makes one REAL call per capability the model is registered for,
so "Connected" means the capability works, not only that the host answers:

* reasoning (text generation)  — a short chat completion (``_probe_chat`` in
  ``app.api.model_registry``, which also reports thinking-model behaviour);
* vision / OCR                 — a chat completion carrying a tiny PNG with the
  word ``HELLO`` drawn in it, asking what it shows (:func:`probe_vision`);
* embeddings                   — ``POST {base}/embeddings`` (in the API module);
* rerank                       — ``POST {base}/rerank`` with one query and two
  documents, reporting the scores (:func:`probe_rerank`).

Every failure carries an ``error_kind`` (:func:`error_kind`) so the UI can say
what is wrong in plain words: the key was rejected, the model is not served,
the endpoint is unreachable, or it does not support the capability.
"""

from __future__ import annotations

import re
import time
from typing import Any

# A 128x48 1-bit PNG with the word HELLO in bold (271 bytes): small enough for
# any vision endpoint's limits, legible enough that an OCR model reads it.
PROBE_IMAGE_WORD = "HELLO"
PROBE_IMAGE_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAIAAAAAwAQAAAADxj08TAAAA1klEQVR42t2SwW3DMAxFnxQB9U3awB2hAxiNR+kmYZE"
    "O0BHaTXTooWPIGzA3BTDMHizb6ArhkXj4fB+gM/6P55EWlm53130wwTSCh4RAwMHXL2BL1MrT1YoZJzMPCVbCWMCDIp"
    "c6EFmiALb0Ws92tWK1L2YeFMY19DltVzIzBiPSMlaiNtNb4j0NRDq6pi6QUSr1WAQc7ATUGaNrRK9yqeCoR0YmEHdC"
    "YWRAIavsYj84KLCLzRhBv7dynITCK66VEwJ8MuEPIlM4S9zqEzJCeXsB98gv9Qd4tGQG6kfNCwAAAABJRU5ErkJggg=="
)

_VISION_PROMPT = "What word is written in this image? Reply with just the word."
_VISION_MAX_TOKENS = 32

# Rerank: one query, a relevant and an irrelevant document. A working reranker
# scores document 0 above document 1.
RERANK_QUERY = "Which city is the capital of France?"
RERANK_DOCUMENTS = (
    "Paris is the capital and largest city of France.",
    "Bananas are a yellow fruit rich in potassium.",
)

ERROR_KINDS = (
    "auth",  # the key was rejected (or none was sent)
    "model_not_served",  # the endpoint answers but does not serve this model id
    "unreachable",  # connection refused / DNS / timeout
    "unsupported",  # the endpoint or model cannot do this capability
    "refused",  # the URL / resolved address is not allowed (egress policy)
    "thinking_budget",  # a thinking model spent the whole budget reasoning
    "invalid_response",  # a 2xx answer without the expected payload
    "http_error",  # any other HTTP failure
)

_HTTP_RE = re.compile(r"HTTP (\d{3})")
_MODEL_MISSING_RE = re.compile(
    r"model[^.]{0,80}?(not found|does not exist|doesn't exist|not exist|not served|"
    r"unknown|is not available|not available|no such)"
    r"|(unknown|invalid|unsupported) model|no such model|model_not_found",
    re.IGNORECASE,
)
_UNSUPPORTED_RE = re.compile(
    r"multimodal|multi-modal|image|vision|not support|unsupported|does not support|"
    r"no endpoint|not implemented|method not allowed",
    re.IGNORECASE,
)


def error_kind(error: str | None) -> str | None:
    """Classify a probe error string (``HTTP 404: ...``, an exception text, ...)."""
    if not error:
        return None
    text = str(error)
    if text.startswith("thinking model:"):
        return "thinking_budget"
    m = _HTTP_RE.search(text)
    status = int(m.group(1)) if m else 0
    if status in (401, 403) or (status == 400 and re.search(r"api[\s_-]?key", text, re.I)):
        return "auth"
    if status and _MODEL_MISSING_RE.search(text):
        return "model_not_served"
    if status in (404, 405, 501):
        return "unsupported"
    if status in (400, 415, 422, 500) and _UNSUPPORTED_RE.search(text):
        return "unsupported"
    if status:
        return "http_error"
    lowered = text.lower()
    if lowered.startswith(("refused", "ssrf")) or "not allowed" in lowered or "egress" in lowered:
        return "refused"
    if lowered.startswith(("the endpoint returned no", "invalid response")):
        return "invalid_response"
    return "unreachable"


def exception_error(exc: BaseException) -> tuple[str, str]:
    """``(error, error_kind)`` for an exception raised while probing."""
    import httpx

    from app.ai_router.model_endpoints import ModelEndpointError

    if isinstance(exc, httpx.TimeoutException):
        return f"timed out: {type(exc).__name__}", "unreachable"
    if isinstance(exc, httpx.HTTPError):
        return (
            f"{type(exc).__name__}: {str(exc)[:300] or 'connection failed'}",
            "unreachable",
        )
    if isinstance(exc, ModelEndpointError):
        return str(exc)[:300], "refused"
    # SSRFError (a ValueError) raised at connect time, or an unparsable body.
    return f"refused or invalid response: {str(exc)[:200]}", "refused"


def check_result(probe: str, capabilities: list[str]) -> dict[str, Any]:
    """An empty per-capability check record."""
    return {
        "probe": probe,
        "capabilities": capabilities,
        "ok": False,
        "latency_ms": 0.0,
        "detail": "",
        "error": None,
        "error_kind": None,
    }


def _elapsed(start: float) -> float:
    return round((time.monotonic() - start) * 1000, 1)


def _http_error(resp: Any) -> str:
    return f"HTTP {resp.status_code}: {str(resp.text)[:300]}"


async def probe_vision(
    client: Any,
    *,
    base: str,
    headers: dict[str, str],
    model_id: str,
    capabilities: list[str],
    mode: str,
    budget: int | None,
    off_by_default: bool,
) -> dict[str, Any]:
    """Send the probe image and ask what it shows (vision / OCR).

    ``ok`` = the endpoint accepted the image and answered. ``text_matched`` says
    whether the answer contains the word drawn in the image (what an OCR model
    must get right); a mismatch is reported, not failed, since a general
    vision model may describe the image instead.
    """
    from app.api.model_registry import _chat_reply
    from app.providers.openai_compatible import thinking_off_body

    out = check_result("vision", capabilities)
    out["expected_text"] = PROBE_IMAGE_WORD
    out["text_matched"] = None
    out["reply"] = ""
    body: dict[str, Any] = {
        "model": model_id,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _VISION_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{PROBE_IMAGE_PNG_B64}"},
                    },
                ],
            }
        ],
        "max_tokens": _VISION_MAX_TOKENS + (budget if mode == "on" and budget else 0),
    }
    off_effective = mode == "off" or (mode == "auto" and off_by_default)
    off_body = thinking_off_body(base, model_id, {**body, "max_tokens": _VISION_MAX_TOKENS})
    first_body = off_body if off_effective and off_body is not None else body
    start = time.monotonic()
    resp = await client.post(f"{base}/chat/completions", json=first_body, headers=headers)
    if resp.status_code >= 400:
        out["latency_ms"] = _elapsed(start)
        out["error"] = _http_error(resp)
        out["error_kind"] = error_kind(out["error"])
        return out
    reply = _chat_reply(resp)
    if (
        not reply["text"]
        and reply["thinking"]
        and mode == "auto"
        and off_body is not None
        and first_body is body
    ):
        retry = await client.post(f"{base}/chat/completions", json=off_body, headers=headers)
        if retry.status_code < 400:
            reply = _chat_reply(retry)
    out["latency_ms"] = _elapsed(start)
    text = str(reply["text"] or "").strip()
    if not text:
        if reply["thinking"]:
            out["error"] = (
                "thinking model: spent the whole budget reasoning about the image without an answer"
            )
        else:
            out["error"] = "the endpoint returned no answer for the image"
            out["error_kind"] = "invalid_response"
        out["error_kind"] = out["error_kind"] or error_kind(out["error"])
        return out
    out["reply"] = text[:120]
    out["text_matched"] = PROBE_IMAGE_WORD.lower() in text.lower()
    out["ok"] = True
    out["detail"] = f"image understood: replied {text[:80]!r}"
    if not out["text_matched"]:
        out["detail"] += f" (expected the word {PROBE_IMAGE_WORD})"
    return out


def parse_rerank_scores(data: Any) -> list[dict[str, Any]]:
    """``[{index, score}]`` from a Cohere / Jina / vLLM style rerank response."""
    if not isinstance(data, dict):
        return []
    items = data.get("results")
    if not isinstance(items, list):
        items = data.get("data")
    scores: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        score = item.get("relevance_score", item.get("score"))
        if (
            isinstance(idx, int)
            and not isinstance(idx, bool)
            and 0 <= idx < len(RERANK_DOCUMENTS)
            and isinstance(score, int | float)
            and not isinstance(score, bool)
        ):
            scores.append({"index": idx, "score": round(float(score), 6)})
    scores.sort(key=lambda s: s["score"], reverse=True)
    return scores


async def probe_rerank(
    client: Any,
    *,
    base: str,
    headers: dict[str, str],
    model_id: str,
    capabilities: list[str],
) -> dict[str, Any]:
    """``POST {base}/rerank`` with one query and two documents; report the scores."""
    out = check_result("rerank", capabilities)
    url = base if base.endswith("/rerank") else f"{base}/rerank"
    start = time.monotonic()
    resp = await client.post(
        url,
        json={"model": model_id, "query": RERANK_QUERY, "documents": list(RERANK_DOCUMENTS)},
        headers=headers,
    )
    out["latency_ms"] = _elapsed(start)
    if resp.status_code >= 400:
        out["error"] = _http_error(resp)
        out["error_kind"] = error_kind(out["error"])
        if out["error_kind"] == "unsupported":
            out["error"] = f"{out['error']} (the endpoint has no working /rerank route)"
        return out
    try:
        data = resp.json()
    except ValueError:
        data = None
    scores = parse_rerank_scores(data)
    if not scores:
        out["error"] = "the endpoint returned no rerank scores"
        out["error_kind"] = "invalid_response"
        return out
    out["scores"] = [{**s, "document": RERANK_DOCUMENTS[s["index"]]} for s in scores]
    out["relevant_first"] = scores[0]["index"] == 0
    out["ok"] = True
    out["detail"] = f"{len(scores)} documents scored" + (
        "" if out["relevant_first"] else " (the relevant document did not rank first)"
    )
    return out
