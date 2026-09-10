# Plan: Concise-planner nudge for verbose reasoning models

**Status:** Deferred — do this LAST.
**Owner:** unassigned
**Depends on:** `dc47902e` (reasoning-aware plan/verifier JSON parsing) — already landed.

## Why

Self-hosted reasoning models (Qwen3.5-4B, DeepSeek-R1, etc.) emit a long preamble —
prose ("The user is asking…", "Thinking Process:…") and/or `<think>…</think>` — *before*
the plan JSON. Observed live against Qwen3.5-4B `:30080`:

- The `{"steps": […]}` object lands at the very end of a 1,700–3,000-char response.
- The planner caps generation at **6000 tokens**; a verbose run can burn the budget on
  preamble and **truncate the JSON before its closing brace** → unrecoverable → the whole
  blob collapses to one raw step.
- Preamble is also pure latency (this is what tripped the 60s LLM timeout) and cost.

The parser fix (`dc47902e`) *recovers* the JSON from preamble in the common case — that is
the safety net. This nudge *prevents* the preamble, which also fixes truncation + speed,
the parser cannot.

## Goal

When a reasoning-style model / concise mode is active, make the planner (and verifier)
emit **only** the JSON — no narration, no `<think>` — so output is short, fast, and
always parses cleanly. Must NOT over-constrain models that already behave well.

## Approach

1. **Config flag** `agent_concise_reasoning: bool = False` (env `AGENT_CONCISE_REASONING`).
   Default off → zero change for existing deployments.
2. **System-prompt suffix** appended to the planner + verifier system prompts when the flag
   is on: e.g. *"Respond with ONLY the JSON object. No explanation, no text before or after
   the JSON, no `<think>` blocks."* Keep it as one shared constant in `app/agent/prompts.py`.
3. **Disable native thinking** where the server supports it: pass
   `extra_body={"chat_template_kwargs": {"enable_thinking": false}}` on the completion
   request (vLLM/Qwen). Thread an optional `extra_body`/`reasoning` field through
   `CompletionRequest` → `OpenAICompatibleProvider.complete()`; ignore it for providers that
   don't accept it. (Some Qwen builds also honor a `/no_think` token in the prompt — lower
   priority, prompt-suffix covers it.)
4. **Optional auto-detect** (stretch): infer concise mode when the model id matches a small
   allowlist (qwen3, deepseek-r1, …) so operators don't have to set the flag. Keep behind the
   same config so it can be forced on/off.

## Out of scope

- Changing the 6000-token planner cap (orthogonal; revisit only if needed after this).
- Non-OpenAI-compatible providers' thinking toggles (add later if a deployment needs it).

## Tasks

- [ ] Add `agent_concise_reasoning` to `app/core/config.py` (+ env).
- [ ] Add a shared `CONCISE_JSON_SUFFIX` constant in `app/agent/prompts.py`; append it to the
      planner + verifier system prompts when the flag is on (in `planner_mixin` / verifier).
- [ ] Add optional `extra_body` (or `reasoning`) to `CompletionRequest`; forward it in
      `OpenAICompatibleProvider.complete()` (and stream) via the OpenAI client's `extra_body`.
- [ ] Wire `enable_thinking: false` into the planner/verifier requests when concise mode is on.
- [ ] (Stretch) model-id auto-detect for reasoning models.
- [ ] Docs: note `AGENT_CONCISE_REASONING=true` in `docs/selfhosted-providers.md`.

## Tests

- [ ] Unit: with the flag on, planner/verifier system prompt contains the concise suffix; off,
      it does not.
- [ ] Unit: `CompletionRequest.extra_body` is forwarded to the OpenAI client call (mock).
- [ ] Unit (mock): a preamble-free `{"steps":[…]}` response parses to N clean steps.
- [ ] Live (opt-in, self-hosted): with concise mode on, Qwen3.5-4B plan output is short
      (< ~400 chars), parses to discrete steps, and the full agent loop completes.

## Acceptance criteria

- Flag OFF ⇒ byte-for-byte identical behaviour to today (all existing tests green).
- Flag ON against a reasoning model ⇒ planner output is JSON-only, no preamble/`<think>`,
  materially shorter, and never truncates at the token cap for a normal goal.
- ruff 0, mypy 0, no regressions.
