# World-class architecture dossier — writer brief

This folder holds deliverables 1–32 and 39 of the owner's "World-class Agentic AI + Knowledge
Platform" program, written about **AgentVerse as it exists on `main` (d8bfb6c7e)** — not an
idealised platform. Live E2E results (33–38, 41, 42) are added later by a separate track.

## Rules

1. **Grounded in code.** Every claim about what the platform does cites the code that does it:
   `agent-verse-backend/app/...py:LINE` (or frontend / infra paths). Read the code; do not trust
   older docs (`docs/`), plans or audits without checking the code. If you cannot find code for a
   capability, it is a **Gap**, not an assumption.
2. **Honest status per capability.** Every capability the owner's prompt lists for your phase gets
   one row: `Implemented` (code path + test that proves it), `Partial` (what is missing), `Gap`
   (absent), or `Out of scope / parked` (owner decisions: org-collab and channels are PARKED;
   MQTT/IoT/streaming triggers are refused; RAFT is beta; Bedrock/Vertex RAFT out of scope; no
   cloud LLM keys or model downloads on this machine — other vendors are exercised via mock
   vendors). Never mark something Implemented because a class exists: it must be wired on a real
   request/goal/worker path.
3. **Mandatory format per topic** (owner's section 33), as headings: A Concept · B Why ·
   C Architecture (mermaid diagram) · D Components (with file paths) · E Data model (real tables /
   pydantic models, migrations) · F APIs (real routes from `app/api/*` / `openapi.json`) · G Events
   (real event names / Redis streams / SSE types) · H Workflow (mermaid sequence diagram of the real
   flow) · I Implementation (key code excerpts, short, with paths — no invented code) · J Testing
   (real test files that cover it, and what is untested) · K Failure modes (the owner's §1.3 list
   that applies: what happens today, with code refs) · L Security · M Scalability · N Observability
   (real metrics / spans / log fields) · O Evaluation · P Cost · Q Production readiness.
4. **End every document with**:
   - `## Capability matrix` — table: capability | status | evidence (code:line, test) | gap note.
   - `## Gaps found` — numbered, each with severity (P0 blocks production / P1 / P2), the exact
     missing piece, and the smallest real fix. These feed deliverable 40 (Final Gap Analysis).
5. **Read-only on code.** Do not edit anything outside your own assigned files in
   `docs/world-class/`. Do not run git commands (no add/commit/stash/checkout). Do not run
   `graphify update`. You may run read-only commands (grep, uv run python -c imports, pytest
   --collect-only) inside this worktree.
6. **No secrets.** Never print or copy values from any `.env` file, API keys or tenant keys.
7. Write for a staff engineer: dense, precise, no filler, no marketing. Prefer tables and diagrams.
   Length is whatever the topic needs (typically 400–1200 lines per document).

## File naming

`NN-slug.md` with the owner's deliverable number, e.g. `01-master-architecture.md`.
