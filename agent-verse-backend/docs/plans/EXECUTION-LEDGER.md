# Execution Ledger — World-Class Chat Platform (autonomous build)

Durable state for the autonomous "complete all 11 phases" effort. Updated every
work increment so progress survives context compaction. Master plan:
`docs/plans/world-class-chat-platform.md`. Branch: `feat/world-class-chat-platform`.

Baseline at start of this effort: **215 passed, 3 skipped** in `tests/chat/`.
Phase 0 (persistence swap) = DONE (commit 62adf50b).

## Rules
- TDD: red→green, commit each working increment, keep the tree green.
- No stubs / no simulation — real implementations wired to existing subsystems.
- Update this ledger's status + "next action" after every increment.

## Phase status (living)
| Phase | Title | Status | Notes |
|---|---|---|---|
| 0 | Spine / persistence | ✅ DONE | committed 62adf50b |
| 1 | Memory & continuity | 🔎 assessing | modules: context/memory_adapter/memory_api exist |
| 2 | Async + delivery-back | 🔎 assessing | adeliver_result done; scheduling exists |
| 3 | Channel unification | 🔎 assessing | channel_message/session exist; identity_links? |
| 4 | Multi-format media I/O | 🔎 assessing | documents/artifact_store/attachments exist |
| 5 | Full-platform skills | 🔎 assessing | skills/ registry + builtin exist |
| 6 | Governance in chat | ⏳ agent assessing | hitl skills exist |
| 7 | World-class frontend | ⏳ agent assessing | 16 orphan components |
| 8 | Voice / phone | ⏳ agent assessing | app/voice/ populated |
| 9 | Proactive outreach | ⏳ agent assessing | app/chat/proactive.py exists |
| 10 | Personal connectors | ⏳ agent assessing | MCP connectors |
| 11 | Personalization | ⏳ agent assessing | profile/style memory? |

## Assessment findings (from agents, authoritative gap map)
- **P1 Memory**: run_qa DOES call build_for_qa + real LLM summarization + memory read/write.
  Gaps: summarization not cached (→ rolling summary), no session-close learning extraction. [IN PROGRESS]
- **P2 Async**: adeliver_result done; SCHEDULE intent + delivery-back exist. Verify acknowledge-now/deliver-later
  (chat_async_jobs) + channel push on completion.
- **P3 Channels**: (tenant,channel,user)→session continuity exists (in-memory map, dormant handler).
  Gap: durable channel→session mapping + identity_links/principal (cross-channel continuity). MISSING.
- **P4 Media**: documents/artifact_store/attachments done + endpoints. Verify audio in/out + KB ingest of attachment.
- **P5 Skills**: registry + builtin (goals/schedules/connectors/docs/approvals) done. Gaps: workflows, models,
  knowledge-base, org-team skills.
- **P6 Governance**: events (hitl/guardrail/cost/usage) + map_goal_event + approval skills DONE.
  Gaps: dedicated `cost` event (folded into usage), verified source=chat AUDIT persistence, governance e2e.
- **P7 Frontend**: 14/16 orphan components unmounted; rich rendering partial (no code highlight/copy, no image/artifact
  cards); reasoning block not rendered; composer missing regenerate/attach/slash/@/voice; no reconnect/backoff;
  154 hardcoded hex; event-name union incomplete. BIG — delegate to subagents in agent-verse-frontend.
- **P8 Voice**: voice engine + webhook adapter DONE but dispatches to OrgService, NOT ChatService. Missing:
  voice_phone.py telephony adapter, STT→ChatService→TTS wiring, outbound task calls, phone-first onboarding.
- **P9 Proactive**: consent/quiet-hours/rate gate (app/chat/proactive.py) DONE. Missing: app/proactive/ engine
  (signal bus → planner → consent gate → multi-channel delivery), audit source=proactive, kill switch, cadence learning.
- **P10 Connectors**: email/calendar/SMS/contacts MCP servers DONE. Missing: maps/location, ride/booking connectors.
- **P11 Personalization**: MISSING entirely — personal profile/style memory/standing instructions/preference learning
  injected into context pipeline.
- **Dual-mode identity**: MISSING — no identity_links table, no unified principal (standalone-individual accounts).

## Execution strategy
Backend phases driven sequentially here (fully testable); Phase 7 frontend delegated to subagents in
the frontend repo (independent). Order: P1 finish → P2 verify/fill → P5 fill (workflows/models/KB/org) →
P6 fill (cost event + audit) → P3 identity_links/principal → P11 personalization → P9 proactive engine →
P10 maps/ride → P8 voice→ChatService+telephony → P4 audio verify → P7 frontend (subagents, parallel).

## Phase progress snapshot
P0 ✅ P1 ✅ P5 ✅(workflows+KB added; models/org-team still open) P9 ✅ P10 ✅ P11 ✅.
Open backend: dual-mode identity/principal (P3 core), P2 verify async/deliver-later, P6 polish,
P8 voice→ChatService+telephony, P5 remaining (models/org-team/connect-oauth skills), durability
migrations for personalization+identity. Frontend P7: agent in flight.

## Next action
Build dual-mode identity: app/identity/ (Principal + IdentityLink + IdentityService,
in-memory now / repo-ready), tests. Then wire into channel-session resolution + migration.

## Background agents in flight
- Frontend rich-output rendering (agent-verse-frontend) — mount output components + code highlight/copy + reasoning block.
- Maps/location + ride/booking MCP connectors (Phase 10).

## Increment log (newest first)
- P5 skills: workflows + knowledge-base adapters. committed 2d8b118d. 223 passed.
- P1 memory: rolling summarization cache + extract_learnings_on_close. committed f56ee003. 219 passed.
- dispatched background agents: frontend rich-output, maps/ride connectors.
- assessment agents returned full gap map (recorded above).
- baseline green 215/3; ledger created; assessment agents dispatched for P7 + Part B.
