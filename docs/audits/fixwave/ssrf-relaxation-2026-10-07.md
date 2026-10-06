# SSRF relaxation audit — every egress check follows ALLOW_PRIVATE_NETWORK_ACCESS (2026-10-07)

Branch `decision/dec-ssrf-all`. Owner requirement (DEC-SSRF): every SSRF guard in the
ecosystem allows private / internal networks while `ALLOW_PRIVATE_NETWORK_ACCESS` is on
(default `true`, every environment). Cloud metadata (169.254.169.254,
metadata.google.internal, fd00:ec2::254, 100.100.100.200), link-local, 0.0.0.0 and
multicast stay blocked (link-local only opens with the existing
`ALLOW_LINK_LOCAL_NETWORK_ACCESS` opt-in). `ALLOW_PRIVATE_NETWORK_ACCESS=false` restores
strict public-only.

## Central policy

The policy lives in `agent-verse-backend/app/net/ssrf_guard.py`. Since 7ee61c00d,
`assert_public_url` applies `private_access_networks()` itself, so every caller below
inherits it, including `request_public`, `resolve_and_check_host`, the pinned
async/sync backends, `connect_public_websocket`, `is_public_url` and `is_ssrf_blocked`.
The checks that stay in place in both modes are the scheme allowlist (http/https,
ws/wss for websockets), redirect re-validation at every hop, connect-time DNS pinning,
the refusal of unix sockets and credential-bearing or path-like hosts, and the
metadata hostname list.

**Gap found and fixed in this audit:** once private access opened `::/0`, metadata
became reachable through IPv6 forms that embed an IPv4 address, where a NAT64 gateway,
6to4 relay, Teredo or SIIT translator would forward the packet to 169.254.169.254.
Examples are `64:ff9b::a9fe:a9fe`, `64:ff9b:1::a9fe:a9fe`, `2002:a9fe:a9fe::1`,
Teredo with client 169.254.169.254, `::a9fe:a9fe` and `::ffff:0:a9fe:a9fe`. All of
these passed with the flag on. `_is_always_blocked_ip` now also checks the embedded
IPv4 addresses (`_embedded_ipv4`).

## Audit table

"Follows" means the check routes through the central guard and already applies the
policy: private allowed when the flag is on, metadata, link-local, 0.0.0.0 and
multicast always blocked, strict when the flag is off. Line numbers refer to this
branch after the change.

### Central guard (`app/net/ssrf_guard.py`)

| Check | file:line | Before | After |
|---|---|---|---|
| `assert_public_url` / `_async` | app/net/ssrf_guard.py:276 | Follows (central default since 7ee61c00d) | Unchanged |
| `_is_always_blocked_ip` (never-reachable set) | app/net/ssrf_guard.py:236 | Ignored metadata embedded in NAT64 / 6to4 / Teredo / IPv4-compatible / SIIT IPv6; reachable with the flag on | **Changed:** also blocks the embedded IPv4 forms of metadata, link-local, 0.0.0.0 and multicast |
| `is_metadata_host` | app/net/ssrf_guard.py:154 | Metadata names and never-reachable literals | Inherits the embedded-IPv4 fix |
| `request_public` (re-validates every redirect hop) | app/net/ssrf_guard.py:420 | Follows | Unchanged |
| `resolve_and_check_host` | app/net/ssrf_guard.py:459 | Follows | Unchanged |
| `PinnedNetworkBackend` / `public_async_client` | app/net/ssrf_guard.py:478, :542 | Follows (connect-time check through `resolve_and_check_host`) | Unchanged |
| `PinnedSyncNetworkBackend` / `public_client` | app/net/ssrf_guard.py:567, :609 | Follows (no `allowed_networks` parameter; the central default applies) | Unchanged |
| `connect_public_websocket` | app/net/ssrf_guard.py:625 | Follows (ws/wss only, dials the checked IP) | Unchanged |
| `is_public_url` / `is_ssrf_blocked` | app/net/ssrf_guard.py:669, :678 | Follows | Unchanged |

### Browsers, RPA and perception

| Check | file:line | Before | After |
|---|---|---|---|
| Browser route and websocket guards (`browser_url_block_reason`, `make_websocket_guard`) | app/net/browser_guard.py:108, :363 | Follows | Unchanged |
| RPA navigation check | app/rpa/executor.py:352 | Follows | Unchanged |
| RPA artifact fetch | app/rpa/executor.py:1034 | Follows | Unchanged |
| `RPA_SSRF_ALLOWED_DOMAINS` | app/rpa/executor.py:114 | Widening-only allowlist; the docstring said "public-only by default" | Docstring corrected |
| Guarded Playwright contexts | app/rpa/session_manager.py:395 | Follows | Unchanged |
| Perception browser | app/perception/browser_agent.py:47 | Follows; the module docstring said private hosts were refused | Docstring corrected |
| `/perception` endpoint | app/api/perception.py:107 | Follows | Unchanged |
| `/rpa/report` endpoint | app/api/rpa.py:173 | Follows | Unchanged |
| `/ingest/rpa-url` endpoint | app/api/knowledge.py:3509 | Follows | Unchanged |

### Agent tools and workflows

| Check | file:line | Before | After |
|---|---|---|---|
| Agent `http_request` tool, literal pre-check | app/tools/http_tool.py:56 | Follows (only `is_metadata_host` when on; old literal blocklist when off) | Class docstring corrected |
| Agent `http_request` tool, resolving check and pinned client | app/tools/http_tool.py:37 | Follows | Unchanged |
| Knowledge-ingest tool | app/tools/knowledge_ingest_tool.py:149 | Follows | Unchanged |
| Workflow `SSRFGuard.validate` | app/workflow/security.py:35 | Follows | Unchanged |
| Workflow HTTP step (pinned client) | app/workflow/steps/http_step.py:87 | Follows | Unchanged |
| Workflow OCR step document fetch | app/workflow/steps/ocr_step.py:188 | Follows (also re-checks each redirect hop) | Unchanged |
| Workflow run callbacks | app/workflow/callbacks.py:60 | Follows | Unchanged |

### A2A and gateway

| Check | file:line | Before | After |
|---|---|---|---|
| A2A outbound call | app/agent/tools/a2a_call.py:147 | Follows | Unchanged |
| A2A callbacks | app/api/a2a.py:345, :457 | Follows | Unchanged |
| A2A remote agent card | app/api/a2a_remote_agents.py:59 | Follows | Unchanged |
| Gateway file download | app/gateway/router.py:290 | Follows | Unchanged |
| SAML SSO URL test | app/api/enterprise.py:2651 | Follows | Unchanged |

### Triggers, notifications and alerts

| Check | file:line | Before | After |
|---|---|---|---|
| API-poll trigger | app/triggers/polling.py:69 | Follows; the docstring said "public host only" | Docstring corrected |
| RSS trigger | app/triggers/rss.py:90 | Follows; the docstring said "public host only" | Docstring corrected |
| SNS certificate fetch | app/triggers/webhooks/sns.py:88 | Follows (the URL must also be an AWS SNS host) | Unchanged |
| Notification webhook channel | app/services/notification_service.py:503 | Follows | Unchanged |
| Alert-router webhook (create and send) | app/observability/alert_router.py:64, :132 | Follows | Unchanged |

### MCP connectors

| Check | file:line | Before | After |
|---|---|---|---|
| MCP HTTP client (register, call, tools/list) | app/mcp/client.py:152 | Follows (`private_access_networks()`) | Unchanged |
| MCP WebSocket transport | app/mcp/ws_client.py:73 | Follows | Unchanged |
| MCP OAuth token URLs | app/mcp/oauth.py:369, :813 | Follows | Unchanged |
| MCP health sweep | app/mcp/health_sweep.py:86 | Follows | Unchanged |
| Built-in MCP connect pinning | app/mcp/servers/egress.py:60 | Follows (through `connector_egress`) | Unchanged |
| Built-in MCP tenant credentials | app/mcp/servers/credentials.py:141 | Follows (through `connector_egress`) | Unchanged |
| Built-in GitHub and Jira servers | app/mcp/servers/github_server.py:132, app/mcp/servers/jira_server.py:272 | Follows | Unchanged |
| Connector API (create, test, OpenAPI fetch) | app/api/connectors.py:596, :976, :1253, :1331 | Follows | Unchanged |

### Ingestion

| Check | file:line | Before | After |
|---|---|---|---|
| `connector_egress._effective_networks` | app/ingestion/connector_egress.py:125 | Follows (`ANY_NETWORK` when on) | Unchanged |
| `assert_source_url`, `assert_source_host`, `assert_source_dsn` | app/ingestion/connector_egress.py:168, :205, :290 | Follows | Unchanged |
| `source_client` and driver resolver pinning (`_checked_lookup`) | app/ingestion/connector_egress.py:152, :523 | Follows | Unchanged |
| `require_pinnable_driver` (librdkafka) | app/ingestion/connector_egress.py:948 | Follows (strict pinning only refuses when the flag is off) | Unchanged |
| Save-time Source config check | app/ingestion/source_egress_policy.py:218 | Follows | Unchanged |
| Repository clones | app/ingestion/repository_security.py:85 | Follows | Unchanged |
| Every URL, host or DSN connector (http, web_crawl, rss, pdf, jira, confluence, gitlab, servicenow, sentry, salesforce, elasticsearch, sharepoint, s3/minio, influxdb, clickhouse, neo4j, mysql, postgresql, mongodb, redis, kafka, mqtt, imap) | app/ingestion/connectors/*.py | Follows (through `connector_egress`) | Unchanged |
| Azure Blob `UseDevelopmentStorage` / `DevelopmentStorageProxyUri` | app/ingestion/connectors/azure_blob_connector.py:82 | **Refused outright**: the Azurite emulator on 127.0.0.1:10000 was blocked even with the flag on | **Changed:** with the flag on, returns `http://127.0.0.1:10000/devstoreaccount1` (plus any proxy URI) to the egress guard and pins it; metadata is still refused; flag off refuses outright as before |
| Azure, Zendesk and SharePoint shape checks (account-name regex, Zendesk `next_page` host, SharePoint tenant GUID) | azure_blob_connector.py, zendesk_connector.py:48, sharepoint_connector.py:59 | URL-shape and host-confusion checks, not private-IP refusals | Unchanged (kept) |

### Model endpoints

| Check | file:line | Before | After |
|---|---|---|---|
| Model Registry endpoint | app/ai_router/model_endpoints.py:75 | Follows | Unchanged |
| Model dispatch | app/providers/model_dispatch.py:180 | Follows | Unchanged |
| Tenant LLM `base_url` check | app/providers/tenant_provider.py:102 | Follows | Unchanged |
| Tenant LLM pinned client | app/providers/tenant_provider.py:84 | Follows | Unchanged |
| OpenAI-compatible SDK pinned client | app/providers/openai_compatible.py:150 | Follows | Unchanged |
| Hosted reranker | app/rag_platform/hosted_reranker.py:79 | Follows; the docstring said "public, blocking RFC-1918" | Docstring corrected |
| Registry reranker | app/rag_platform/registry_reranker.py:148 | Follows | Unchanged |
| RAFT fine-tune endpoint | app/rag/raft_compat_provider.py:234 | Follows | Unchanged |
| Web-augmented RAG fetch | app/rag/agentic/patterns/web_augmented.py:233, :465 | Follows (its domain allowlist is a feature filter, not an IP check) | Unchanged |

### Configuration

| Check | file:line | Before | After |
|---|---|---|---|
| Production refusal of CIDR entries in `INGESTION_INTERNAL_SOURCE_ALLOWLIST` | app/core/config.py:820 | **Startup failed** in production when the allowlist held a CIDR, even with the flag on, where the entries are moot | **Changed:** refuses only when `allow_private_network_access` is false |

### Frontend (`agent-verse-frontend/src/`)

There is no client-side IP or hostname validation. All three changes are wording only.

| Check | file:line | Before | After |
|---|---|---|---|
| `friendlyConnectionError` SSRF message | src/lib/friendlyError.ts:24 | "connections to private, internal or loopback hosts are not allowed" | **Changed:** names the egress policy: metadata and link-local never, private only when enabled |
| Elasticsearch form hint | src/features/ingestion/components/families/ElasticsearchForm.tsx:26 | "internal addresses are refused unless the operator allowlists them" | **Changed** to match the policy |
| Workflow browser-step URL description | src/features/workflow/builder/WorkflowStepConfig.tsx:799 | "Internal/loopback hosts are blocked" | **Changed** to match the policy |

### Out of scope or no check present

None of these refuse private addresses, so there was nothing to relax.

| Path | file:line | Note |
|---|---|---|
| Inbound IP allowlists (API keys, RBAC, proxy trust) | app/auth/ip_allowlist.py:131, app/tenancy/rbac.py:172, app/auth/scope_enforcement.py:424, app/api/tenants.py:915 | Inbound client-IP checks, not egress |
| Sandbox network modes | app/sandbox_runtime/network_policy.py, app/execution_environment/models.py:43 | Sandbox isolation (deny-all / allowlist), not an SSRF guard |
| SIEM adapters, OTEL exporters, SearXNG, voice / TTS, Slack integration | app/governance/siem_adapters.py:106 and others | Operator-configured or fixed vendor hosts; no guard |
| `WebhookService` | app/services/webhook_service.py:116 | Plain `httpx` with no guard. No references outside the module (appears dead) |
| Email tool SMTP host from the tenant vault | app/tools/email_tool.py:196 | No egress guard at all, so metadata is not blocked either. Pre-existing gap, not changed here |
| ~300 built-in MCP vendor servers using plain `httpx` | app/mcp/servers/*_server.py | Pinned at connect time by app/mcp/servers/egress.py on tenant calls (see above) |

## Tests

* `tests/net/test_ssrf_ecosystem_policy.py` (new, 165 cases). It runs 18 public guard
  entry points (`assert_public_url`, `assert_public_url_async`, `is_public_url`,
  `is_ssrf_blocked`, `resolve_and_check_host`, `request_public`, both pinned backends,
  `connect_public_websocket`, `browser_url_block_reason`, workflow `SSRFGuard`,
  http_tool `_is_blocked`, `assert_source_url`, `assert_source_host`,
  `assert_source_config_egress`, MCP `checked_addresses`, `check_model_endpoint`,
  tenant LLM base_url) against 10.0.0.5, 192.168.63.104, 127.0.0.1 and 169.254.169.254
  under both flag values. It also covers the IPv6-embedded metadata forms in both
  modes, embedded private IPv4 following the flag, an allowlisted name resolving to
  embedded metadata, and public IPv6 that must not be caught by the embedded check.
* `tests/ingestion/test_azure_blob_egress.py`: Azurite reaches the SDK with the flag on,
  a private `BlobEndpoint` passes with the flag on, metadata (including through the
  proxy URI) stays blocked with the flag on, and development storage is refused with
  the flag off.
* `tests/ingestion/test_connector_egress_networks.py`: production accepts CIDR entries
  with the flag on and refuses them with the flag off.
* `src/lib/friendlyError.test.ts`: the refusal message names the egress policy and no
  longer claims a blanket private-host ban.
