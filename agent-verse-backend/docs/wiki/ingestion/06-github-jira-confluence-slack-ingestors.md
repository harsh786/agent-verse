---
title: "GitHub, Jira, Confluence, and Slack Ingestors"
description: "How AgentVerse ingests GitHub repos, Jira issues, Confluence spaces, and Slack channels into the knowledge store — authentication, chunking, metadata, incremental sync, and real-world patterns."
outline: deep
---

# GitHub, Jira, Confluence, and Slack Ingestors

Developer and team platform ingestion is the bridge between where engineers actually work — code repos, issue trackers, wikis, and chat — and the knowledge store that agents reason over. Without it, an engineering agent answering "how does the retry logic work?" must either hallucinate or fail; with it, the agent retrieves the authoritative source from the actual codebase.

These four ingestors specialise in **API-driven, incremental content pull** from the platforms that hold most of an engineering team's institutional knowledge.

<!-- Sources: app/knowledge/ingestors/ -->

---

## Overview

### Why developer platform ingestion is different

File-based ingestors (PDF, DOCX, code uploads) handle static blobs. Developer platform ingestors handle **living content** — repos that change on every commit, Jira boards updated throughout the day, Confluence pages edited by dozens of contributors. Three challenges distinguish them:

1. **Authentication** — OAuth tokens or API keys with per-tenant scoping, stored in the vault, rotated independently per connector.
2. **API rate limits** — GitHub allows 5,000 authenticated requests/hour; Jira and Confluence impose per-minute quotas; Slack has tiered limits. Each ingestor must paginate carefully and respect back-off signals.
3. **Incremental sync** — Re-ingesting a 500-file repo from scratch hourly is wasteful. Ingestors support content-hash deduplication and `ORDER BY updated DESC` patterns to avoid redundant work.

### Ingestor reference table

| Ingestor | Class | Auth Type | What is ingested | Chunk size | Key metadata fields | Rate limit |
|---------|-------|-----------|-----------------|-----------|---------------------|-----------|
| GitHub | `GitHubIngestor` | Bearer token (`GITHUB_TOKEN`) | Repo files (text/code only) | 1,500 chars / 100 overlap | `owner`, `repo`, `path`, `branch`, `size` | 5,000 req/hr (authenticated) |
| Jira | `JiraIngestor` | HTTP Basic (base64 `user:token`) | Issues, comments (ADF→text) | 1 issue + up to 5 comments | `key`, `summary`, `status`, `project` | ~1 req/s (Cloud) |
| Confluence | `ConfluenceIngestor` | HTTP Basic (user + API token) | Pages (HTML→text, chunked) | 1,200 chars / 100 overlap | `title`, `space`, `space_key`, `page_id` | ~2 req/s (Cloud) |
| Slack | `SlackIngestor` | Bearer token (bot OAuth `xoxb-`) | Channel messages (5-msg windows) | 5 messages / chunk | `channel_id`, `channel_name` | Tier 3: 50 req/min |

### Architecture: source → chunks → retrieval

```mermaid
flowchart LR
    GH["GitHub\nREST API v2022-11-28"]
    JI["Jira\nREST API v3"]
    CF["Confluence\nREST API v1"]
    SL["Slack\nWeb API"]
    ING["Ingestor Layer\nGitHubIngestor · JiraIngestor\nConfluenceIngestor · SlackIngestor"]
    KS["KnowledgeStore\ningest_chunks_async()"]
    VEC["Vector Index\npgvector (per-tenant)"]
    RET["Retrieval\nRAG hybrid search"]
    AGT["Agent Loop\nPlan → Execute → Verify"]

    GH --> ING
    JI --> ING
    CF --> ING
    SL --> ING
    ING --> KS
    KS --> VEC
    VEC --> RET
    RET --> AGT

    style GH fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style JI fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style CF fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style SL fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style ING fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
    style KS fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style VEC fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style RET fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style AGT fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
```

<!-- Sources: app/knowledge/ingestors/github_ingestor.py, jira_ingestor.py, confluence_ingestor.py, slack_ingestor.py -->

---

## GitHub Ingestor (`GitHubIngestor`)

### What it ingests

`GitHubIngestor` walks a repository's full file tree via the GitHub Git Trees API (`/repos/{owner}/{repo}/git/trees/HEAD?recursive=1`) and fetches the raw content of every file that passes two filters:

**Skip — always excluded**

| Category | Values |
|---------|--------|
| `_SKIP_DIRS` | `.git`, `.github`, `node_modules`, `__pycache__`, `.venv`, `venv`, `dist`, `build`, `.next`, `coverage`, `.cache`, `vendor` |
| `_SKIP_EXTENSIONS` | `.png`, `.jpg`, `.jpeg`, `.gif`, `.svg`, `.ico`, `.webp`, `.bin`, `.zip`, `.tar`, `.gz`, `.pdf`, `.woff`, `.ttf`, `.mp4`, `.mp3`, `.avi`, `.mov`, `.exe`, `.dll`, `.so`, `.dylib` |

**Ingest — text and code only** (`_TEXT_EXTENSIONS`): `.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs`, `.md`, `.mdx`, `.txt`, `.rst`, `.adoc`, `.yaml`, `.yml`, `.json`, `.toml`, `.ini`, `.cfg`, `.env.example`, `.sh`, `.bash`, `.zsh`, `.fish`, `.go`, `.rs`, `.java`, `.rb`, `.php`, `.cs`, `.cpp`, `.c`, `.h`, `.hpp`, `.kt`, `.swift`, `.html`, `.htm`, `.css`, `.scss`, `.less`, `.sql`, `.graphql`

Files smaller than 50 characters after stripping are skipped. Per-file content is capped at **100 KB** to bound memory usage.

### Authentication

```python
GitHubIngestor(token="ghp_xxxxx")          # explicit
GitHubIngestor()                            # falls back to GITHUB_TOKEN env var
```

Every request sets `Authorization: Bearer {token}` plus `Accept: application/vnd.github+json` and `X-GitHub-Api-Version: 2022-11-28`.

### Chunking

Sliding-window chunking with `_CHUNK_SIZE = 1500` characters and `_CHUNK_OVERLAP = 100` characters. For a 4,500-char file this produces three chunks: `[0:1500]`, `[1400:2900]`, `[2800:4300]`.

Each chunk carries:

```python
{
    "content": "<chunk text>",
    "source_url": "https://github.com/{owner}/{repo}/blob/{branch}/{path}",
    "source_type": "github",
    "source_doc_id": "{owner}/{repo}/{path}",
    "page_number": None,
    "metadata": {
        "owner": owner, "repo": repo,
        "path": path, "branch": branch, "size": <bytes>,
    },
}
```

### Large repo handling

If the recursive tree exceeds GitHub's 100,000-node limit, the API sets `truncated: true`. The ingestor logs a `github_tree_truncated` warning with `owner` and `repo` and continues with the partial tree. For very large monorepos, use the `max_files` parameter (default: 300) and `file_patterns` to restrict scope.

### Sequence: `ingest_repo()`

```mermaid
sequenceDiagram
    participant C as Caller
    participant G as GitHubIngestor
    participant API as GitHub REST API
    participant RAW as raw.githubusercontent.com

    C->>G: ingest_repo(owner, repo, max_files=300)
    G->>API: GET /repos/{owner}/{repo}/git/trees/HEAD?recursive=1
    API-->>G: tree[] (up to 100K nodes)
    Note over G: filter: type=blob, _should_ingest(path)
    loop For each text/code file
        G->>RAW: GET /{owner}/{repo}/HEAD/{path}
        RAW-->>G: raw content (capped at 100KB)
        Note over G: sliding window chunks (1500/100)
        G->>G: append chunk dicts to result
    end
    G-->>C: list[dict] (N chunks)
```

### Real-world example 1 — engineering agent answers codebase questions

**Scenario:** A 150-person startup ingests their main API repo (500 files) into the `engineering` knowledge collection nightly.

```python
chunks = await GitHubIngestor(token=vault.get("GITHUB_TOKEN")).ingest_repo("acmecorp", "api-service", max_files=400)
await knowledge_store.ingest_chunks_async(chunks, collection_id="engineering", tenant_ctx=ctx)
```

An engineer asks: *"How does the payment service handle retries?"* The agent retrieves three chunks from `app/services/payment.py` and `app/core/retry.py`, returns an exact answer with file-path citations and links to the GitHub blob URL.

**Outcome:** 400 files → ~1,200 chunks → P50 answer latency ~320 ms.

### Real-world example 2 — DevOps agent answers infrastructure questions

**Scenario:** A platform team ingests their Terraform repo. An SRE asks *"What Terraform modules are used for the EU region?"* The agent retrieves chunks from `modules/eu/main.tf` and `environments/prod-eu/*.tf`, listing all VPC, RDS, and EKS modules with version pins.

**Scalability:** GitHub authenticated rate limit is 5,000 req/hr per token (~5 requests/repo for tree + file fetches), supporting ~1,000 repo ingests/day per token. GitHub App installations per tenant provide 15,000 req/hr each, scaling to unlimited tenants.

<!-- Sources: app/knowledge/ingestors/github_ingestor.py -->

---

## Jira Ingestor (`JiraIngestor`)

### What it ingests

`JiraIngestor` fetches issues from a Jira project via the REST API v3 `/rest/api/3/search` endpoint, retrieving `summary`, `description`, `status`, `assignee`, `priority`, `comment`, `labels`, `created`, and `updated`. Descriptions in **Atlassian Document Format (ADF)** are recursively converted to plain text via `_adf_to_text()`.

Each issue becomes **one chunk** (issue key + title + status + priority + truncated description). Up to **5 comments** per issue become separate chunks.

### Authentication

Jira Cloud and Server both use HTTP Basic Authentication with a base64-encoded `user:api_token` pair:

```python
JiraIngestor(
    base_url="https://acmecorp.atlassian.net",
    user="jane@acmecorp.com",
    token="ATATT3xFfGF0..."     # Atlassian API token from id.atlassian.com
)
```

The ingestor builds the `Authorization: Basic <b64>` header internally via `_make_basic()`.

### JQL query construction

`ingest_project()` builds a JQL query that is extendable via the `jql_extra` parameter:

```python
# Default: all issues in project, newest first
jql = f"project = {project_key} ORDER BY updated DESC"

# Custom: only open bugs in the last 30 days
jql_extra = "issuetype = Bug AND status != Done AND created >= -30d"
chunks = await ingestor.ingest_project("SUPPORT", jql_extra=jql_extra)
```

Pagination uses `startAt` / `maxResults` (50 per page), advancing until fewer than 50 results are returned or `max_issues` (default: 500) is reached.

### ADF to plain text

Jira Cloud descriptions use the Atlassian Document Format — a nested JSON structure. `_adf_to_text()` recursively traverses the node tree, joining `text` leaves with newlines at block boundaries (`paragraph`, `heading`, `bulletList`, `orderedList`) and spaces within inline spans:

```python
# ADF → "Set up the payment gateway.\n- Configure webhook\n- Test with Stripe sandbox"
text = JiraIngestor._adf_to_text(adf_description)
```

### Sequence: `ingest_project()`

```mermaid
sequenceDiagram
    participant C as Caller
    participant J as JiraIngestor
    participant API as Jira REST API v3

    C->>J: ingest_project("SUPPORT", max_issues=500)
    Note over J: jql = "project = SUPPORT ORDER BY updated DESC"
    loop Paginate (50 issues/page)
        J->>API: GET /rest/api/3/search?jql=...&startAt=N&maxResults=50
        API-->>J: issues[] with fields
        loop For each issue
            Note over J: _adf_to_text(description) if ADF
            J->>J: append issue chunk (key + summary + status + desc[:2000])
            loop Up to 5 comments
                J->>J: append comment chunk
            end
        end
        Note over J: break if issues < 50 or limit reached
    end
    J-->>C: list[dict] (issues + comment chunks)
```

### Real-world example 1 — support agent answers bug frequency questions

**Scenario:** A support agent ingests the `SUPPORT` Jira project (12,000 issues, 3 years). A support engineer asks: *"How many times has the payment timeout issue been reported?"* The agent retrieves 23 matching issue chunks, counts distinct keys, and responds with the most recent issue key and a known workaround.

### Real-world example 2 — PM agent surfaces roadmap status

**Scenario:** Product roadmap lives in `ROADMAP` as Epics/Stories with quarter labels.

```python
chunks = await ingestor.ingest_project("ROADMAP", jql_extra="issuetype in (Epic, Story)")
```

A PM asks: *"What features are planned for Q3?"* The agent retrieves Epic chunks labelled `Q3-2026`, summarises by team, and flags three at-risk items.

<!-- Sources: app/knowledge/ingestors/jira_ingestor.py -->

---

## Confluence Ingestor (`ConfluenceIngestor`)

### What it ingests

`ConfluenceIngestor` ingests all current pages in a Confluence space by calling `/rest/api/content` with `expand=body.storage,version,space,ancestors`. The `body.storage` field contains the page body as Confluence Storage Format HTML, which `_html_to_text()` converts to plain text.

### Authentication

```python
ConfluenceIngestor(
    base_url="https://acmecorp.atlassian.net/wiki",
    user="jane@acmecorp.com",
    token="ATATT3xFfGF0..."    # same Atlassian API token as Jira
)
```

The ingestor uses `httpx`'s native `auth=(user, token)` tuple, which sends `Authorization: Basic <b64>` automatically.

### HTML-to-text conversion

`_html_to_text()` is a three-pass normaliser:

1. **Strip tags:** `re.sub(r'<[^>]+>', ' ', html)` — replaces all HTML tags with a space.
2. **Decode entities:** `html.unescape(text)` — `&amp;` → `&`, `&lt;` → `<`, `&#8212;` → `—`.
3. **Normalise whitespace:** `re.sub(r'\s+', ' ', text).strip()` — collapses all runs of whitespace.

This intentionally discards Confluence macros (code blocks rendered as `<ac:structured-macro>`), table structure, and inline formatting — preserving only the searchable text content.

### Chunking

`_CHUNK_SIZE = 1200` characters with an effective overlap of 100 characters (advance step is 1,100). The **first chunk** of each page is prefixed with the Markdown heading `# {title}\n\n`, making the chunk self-describing for retrieval:

```python
chunk_text = f"# {title}\n\n{chunk}" if start_pos == 0 else chunk
```

Each chunk carries:

```python
{
    "content": "# How to Deploy to Production\n\n<first 1200 chars>",
    "source_url": "{base_url}/pages/{page_id}",
    "source_type": "confluence",
    "source_doc_id": page_id,
    "page_number": None,
    "metadata": {
        "title": title, "space": space_name,
        "space_key": space_key, "page_id": page_id,
    },
}
```

### Sequence: `ingest_space()`

```mermaid
sequenceDiagram
    participant C as Caller
    participant CF as ConfluenceIngestor
    participant API as Confluence REST API v1

    C->>CF: ingest_space("RUNBOOKS", max_pages=1000)
    loop Paginate pages (50/batch)
        CF->>API: GET /rest/api/content?spaceKey=RUNBOOKS&expand=body.storage,...
        API-->>CF: pages[] with HTML body
        loop For each page
            Note over CF: _html_to_text(html) — strip tags, decode entities, collapse whitespace
            Note over CF: skip if text < 50 chars
            loop Sliding chunks (1200/1100)
                Note over CF: prefix first chunk with "# {title}"
                CF->>CF: append chunk dict
            end
        end
        Note over CF: break if pages < 50 or max reached
    end
    CF-->>C: list[dict] (N chunks)
    Note over CF: log confluence_space_ingested {space, pages, chunks}
```

### Real-world example 1 — HR agent answers policy questions

**Scenario:** An HR agent ingests the `POLICIES` space (200 pages: leave, benefits, security, code of conduct). A new hire asks: *"What is the vacation policy for remote employees?"* The agent returns the "Remote Work & PTO" chunk and cites it with a `/pages/{page_id}` link.

### Real-world example 2 — engineering agent answers runbook questions

**Scenario:** The SRE team maintains 80 runbooks in `RUNBOOKS`. An on-call engineer asks at 2 AM: *"How do I rotate the database credentials without downtime?"* The agent returns the exact step-by-step procedure from the "Database Credential Rotation" runbook page.

**Breadcrumb note:** The `ancestors` field is fetched in the API call (`expand=...ancestors`) but not yet propagated to chunk metadata. A future enhancement could add `ancestor_titles` (e.g., `Engineering > Operations > Databases`) for contextual breadcrumb citations.

<!-- Sources: app/knowledge/ingestors/confluence_ingestor.py -->

---

## Slack Ingestor (`SlackIngestor`)

### What it ingests

`SlackIngestor` fetches message history from a Slack channel using the `conversations.history` Web API endpoint. It processes messages in order of recency (API default), skipping bot messages and system subtypes (`msg.get("subtype")` check), grouping every **5 messages** into a single chunk.

### Authentication

```python
SlackIngestor(token="xoxb-123456-abcdef")   # Bot OAuth token
```

The bot app must have the `conversations:history` OAuth scope. For private channels, `channels:history`; for public, `conversations:history` is sufficient.

### Message processing and chunking

Messages shorter than 10 characters are skipped (reactions, single-emoji messages). The 5-message window accumulates messages into `"\n".join(window)`, then flushes to a chunk:

```python
{
    "content": "message1\nmessage2\nmessage3\nmessage4\nmessage5",
    "source_url": "https://slack.com/archives/{channel_id}",
    "source_type": "slack",
    "source_doc_id": "{channel_id}/{last_message_ts}",
    "page_number": None,
    "metadata": {"channel_id": channel_id, "channel_name": channel_name},
}
```

Any remaining messages in the final partial window are flushed as the last chunk.

### Pagination

Slack `conversations.history` returns up to 200 messages per call with a `next_cursor` in `response_metadata`. The ingestor follows cursors until either the `max_messages` limit (default: 500) is reached or `next_cursor` is absent:

```python
params = {"channel": channel_id, "limit": 200}
if cursor:
    params["cursor"] = cursor
# ...
cursor = data["response_metadata"].get("next_cursor")
if not cursor or message_count >= max_messages:
    break
```

### Sequence: `ingest_channel()`

```mermaid
sequenceDiagram
    participant C as Caller
    participant SL as SlackIngestor
    participant API as Slack Web API

    C->>SL: ingest_channel("C01234ABCDE", channel_name="incidents")
    loop Paginate (200 messages/page, cursor)
        SL->>API: GET /api/conversations.history?channel=...&limit=200[&cursor=...]
        API-->>SL: {ok: true, messages[], response_metadata: {next_cursor}}
        loop 5-message windows
            Note over SL: skip subtype messages, skip text < 10 chars
            SL->>SL: accumulate window[]
            Note over SL: flush window → chunk when len(window) == 5
        end
        Note over SL: flush remaining partial window
        Note over SL: break if next_cursor absent or max_messages reached
    end
    SL-->>C: list[dict] (N chunks)
```

### Real-world example 1 — customer success agent surfaces feature requests

**Scenario:** A team ingests `#customer-feedback` monthly (5,000 messages/month). A PM asks: *"What features are most requested this quarter?"* The agent retrieves 47 relevant chunks, clusters by topic (dark mode: 12, API webhooks: 9, CSV export: 7), and delivers a ranked demand report.

### Real-world example 2 — incident response agent reconstructs post-mortems

**Scenario:** `#incidents` holds real-time RCA threads. The SRE team ingests it weekly. An SRE asks: *"What was the root cause of the June 15 outage?"* The agent retrieves the message window containing: *"Root cause: Redis memory exhaustion due to misconfigured TTL. Fix: maxmemory-policy allkeys-lru. Duration: 47 minutes."*

### Rate limiting

Slack's `conversations.history` operates under **Tier 3** (50 requests/minute). At 200 messages/request, this yields 10,000 messages/minute before throttling. For channels with very high volume (100K+ messages), implement exponential back-off on HTTP 429 responses and consider processing in daily batch windows. The ingestor does not implement back-off natively — callers should wrap `ingest_channel()` with a retry loop for high-volume production use.

<!-- Sources: app/knowledge/ingestors/slack_ingestor.py -->

---

## Integration with KnowledgeStore and MCP

### Ingestion pipeline integration

All four ingestors return the same chunk format (`list[dict]`) that `KnowledgeStore.ingest_chunks_async()` accepts directly:

```python
# GitHub: nightly full-repo ingest
gh_chunks = await GitHubIngestor(token=...).ingest_repo("acmecorp", "api-service")
await ks.ingest_chunks_async(gh_chunks, collection_id="codebase", tenant_ctx=ctx)

# Jira: incremental (last 7 days)
ji_chunks = await JiraIngestor(...).ingest_project("SUPPORT", jql_extra="updated >= -7d")
await ks.ingest_chunks_async(ji_chunks, collection_id="jira", tenant_ctx=ctx)

# Confluence: full space ingest
cf_chunks = await ConfluenceIngestor(...).ingest_space("ENG")
await ks.ingest_chunks_async(cf_chunks, collection_id="confluence", tenant_ctx=ctx)

# Slack: recent 500 messages
sl_chunks = await SlackIngestor(token=...).ingest_channel("C01234ABCDE")
await ks.ingest_chunks_async(sl_chunks, collection_id="slack", tenant_ctx=ctx)
```

### MCP server integration

Each ingestor has a companion MCP server that exposes **live API tools** via the MCP protocol, enabling agents to query and mutate these platforms in real time — beyond what is available in the ingested knowledge store:

| Ingestor | MCP Server | Key tools |
|---------|-----------|-----------|
| `GitHubIngestor` | `app/mcp/servers/github_server.py` | `github_list_repos`, `github_get_file`, `github_list_issues`, `github_create_issue` |
| `JiraIngestor` | `app/mcp/servers/jira_server.py` | `jira_search_issues`, `jira_get_issue`, `jira_create_issue` |
| `ConfluenceIngestor` | `app/mcp/servers/confluence_server.py` | Live Confluence page reads |
| `SlackIngestor` | `app/mcp/servers/slack_server.py` | `slack_send_message`, `slack_list_channels`, `slack_get_channel_history`, `slack_search_messages` |

### When to use ingestion vs live MCP

| Scenario | Use Ingestion | Use MCP |
|---------|:---:|:---:|
| "What does the retry function do?" (code search) | ✅ | — |
| "List all open bugs in PROJ-123's sprint" (current state) | — | ✅ |
| "How many times has X bug been reported?" (history count) | ✅ | — |
| "Create a Jira issue for this regression" (write) | — | ✅ |
| "What did the team discuss about auth last month?" (past Slack) | ✅ | — |
| "Send the deployment status to #deploys" (send message) | — | ✅ |
| Full-text search across all repo files | ✅ | — |
| Get the latest version of a single file | — | ✅ |

### Combined pattern: ingestion + MCP

A production-grade DevOps agent combines both:

```
1. [Nightly] GitHubIngestor ingests infra repo → KnowledgeStore
2. Goal: "Find the last 3 failed deployments and open Jira tickets"
3. Agent queries KnowledgeStore: "deployment failure patterns"
4. Agent calls MCP: github_list_issues(repo="infra", label="deploy-failed")
5. Agent calls MCP: jira_create_issue(project="OPS", summary="Deploy failure ...")
```

The ingestion provides context and history; MCP provides real-time state and write access.

<!-- Sources: app/mcp/servers/github_server.py, app/mcp/servers/jira_server.py, app/mcp/servers/slack_server.py -->

---

## Scalability and Operations

### Deduplication

All four ingestors return raw chunk dicts. Deduplication is performed by the `KnowledgeStore` ingestion layer via SHA-256 hash of normalised chunk content. Re-ingesting an unchanged GitHub file, Jira issue, or Confluence page produces zero new database writes.

For Jira, the `ORDER BY updated DESC` sort combined with a `jql_extra="updated >= -7d"` filter makes weekly incremental syncs efficient — only recently modified issues are re-fetched.

### Incremental sync scheduling

Recommended Celery Beat schedules per ingestor:

| Ingestor | Recommended Schedule | Trigger condition |
|---------|---------------------|------------------|
| GitHub | Nightly (02:00 UTC) + on `push` webhook | `ingest_repo()` per active repo |
| Jira | Every 4 hours | `jql_extra="updated >= -4h"` |
| Confluence | Daily (03:00 UTC) | Full `ingest_space()` (idempotent) |
| Slack | Every 2 hours | `max_messages=500` per active channel |

### Rate limit management

Per-connector safe polling rates and back-off strategy:

| Connector | Rate | Recommended back-off |
|-----------|------|-----------------------|
| GitHub | 5,000 req/hr (~1.4/s) | 1 s between large-repo file fetches |
| Jira Cloud | ~1 req/s | 1 s sleep between pages |
| Confluence Cloud | ~2 req/s | 500 ms sleep between pages |
| Slack Tier 3 | 50 req/min | Exponential back-off on HTTP 429 |

On HTTP 429: `wait = min(2 ** retry * 0.5, 60)` seconds.

### Monitoring

Key Prometheus metrics: `ingestor_chunks_created{source}` (counter), `ingestor_files_processed{source}` (counter), `ingestor_run_duration_seconds{source}` (histogram), `ingestor_api_errors_total{source,status_code}` (counter), `ingestor_rate_limit_hits{source}` (counter), `ingestor_truncated_repos_total` (counter — GitHub 100K node limit hits). These map to the standard `ingest_latency_ms` histogram and `ingest_error_count` counter in AgentVerse observability.

### Multi-tenant credential isolation

Each tenant stores credentials in the vault under connector-scoped keys:

```
{tenant_id}/connectors/github/token
{tenant_id}/connectors/jira/api_token
{tenant_id}/connectors/confluence/api_token
{tenant_id}/connectors/slack/bot_token
```

KnowledgeStore namespacing (per-tenant `collection_id` + Postgres RLS) ensures chunks from one tenant's GitHub repo are never retrievable by another tenant's agent.

<!-- Sources: app/governance/vault.py, app/db/rls.py -->
