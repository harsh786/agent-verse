# Leftover uncommitted worktree work — triage (2026-10-05)

Full uncommitted diffs saved here as `<worktree>.patch`. Verdicts vs main `efb67e023`:

| Worktree | Item | Verdict | Disposition |
|---|---|---|---|
| wf_01f33451-68a-1 | system jobs on maintenance role | SUPERSEDED | main 1e0942ab6 |
| wf_01f33451-68a-2 | ingestion scans/DLQ roles | SUPERSEDED | main 826f92c7c |
| wf_01f33451-68a-3 | governance startup under RLS | SUPERSEDED | main fdb076128 |
| wf_01f33451-68a-4 | civilization/skills/notification RLS | SUPERSEDED | main bd64943cf (+OPS-34) |
| agent-a3c87f9f0b77b9eec | real-world KB corpus harness | SUPERSEDED | main 199cec12a / 8f3630f09 |
| agent-a696307d9338941d4 | SAML-01 early start | DISCARD | subsumed by a0a0 |
| agent-a9c06085c934d55b1 | GDPR export backend | SUPERSEDED (backend) | main 2e4f13753 / 90450073a; UI banner → leftover/smallfix |
| agent-aada7be70ab209938 | MCP health sweep | SUPERSEDED | main 3e31d3df8; partial index → leftover/smallfix |
| agent-a0a0da5120f3bd136 | SAML-01 session issuance | PARTIAL-VALUABLE (M) | leftover/saml01 |
| agent-a5eba6cf1a2bea89a | SVC-05 SSE durable seq ids | PARTIAL-VALUABLE (M) | leftover/svc05 |
| agent-a32d3365d274c1a3c | OPS-37 streaming training export + jobs | PARTIAL-VALUABLE (M) | leftover/ops37 |
| agent-abe3355f78e78c215 | KB-44 scalable upstream-deletion reconcile | PARTIAL-VALUABLE (M) | leftover/kb44 |
| agent-a737d3e48c33e7b25 | CORE-18 durable strategy checkpoints | PARTIAL-VALUABLE (S) | leftover/smallfix |
| agent-a815bab79cb97d102 | WF-TIMEOUT-MISREPORT | PARTIAL-VALUABLE (S) | leftover/smallfix |
| agent-a895c7a18bd42c3cf | a08-F188-02 dead channel code | PARTIAL-VALUABLE (S) | leftover/smallfix |
| agent-g08be | ORG-32 chat search in PG mode | PARTIAL-VALUABLE (S) | leftover/smallfix |
| g08fe | FE-01 e-stop counts + status | PARTIAL-VALUABLE (S) | leftover/smallfix |
