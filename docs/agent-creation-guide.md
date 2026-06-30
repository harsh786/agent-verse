# AgentVerse: Complete Agent Creation Masterplan
## Simple JIRA Agent → Multi-Connector → Multi-Agent → Civilization

> **The end-to-end practical guide.** Every step verified against real AgentVerse code.
> Built from actual connector setup, auth debugging, and platform architecture.

---

## Quick Status Check

Before starting, verify everything is running:

```bash
# 1. Backend healthy?
curl -s http://localhost:8000/health | python3 -m json.tool
# Expected: {"status":"healthy","checks":{"postgres":{"status":"up"},"redis":{"status":"up"}}}

# 2. Your API key (from earlier setup)
export AV_KEY="av_free_CvkhCJyL3OSJy6mWUe_2hpZCotGWYFYvzhg0xs-K-WQ"

# 3. Frontend running?
open http://localhost:5173
```

If backend is down:
```bash
colima start
cd agent-verse-backend
docker-compose -f infra/docker-compose.yml up -d postgres redis
uv run uvicorn app.main:app --reload --port 8000 &
uv run celery -A app.scaling.celery_app worker --loglevel=info -Q goals,schedules,maintenance --concurrency=2 &
```

---

# PHASE 1: Simple JIRA Agent
**Time to complete: 30 minutes**
**What you build: An agent that reads, creates, and triages JIRA tickets autonomously**

---

## Step 1.1 — Register JIRA Connector

### Via the new UI (Fixed auth form)

1. Go to **http://localhost:5173/connectors**
2. Click **+ Register Connector**
3. Fill in:

| Field | Value |
|-------|-------|
| **Name** | `pinelabs-jira` |
| **URL** | `https://pinelabs.atlassian.net` ← auto-filled when you type "jira" |
| **Auth Type** | `Basic Auth` |
| **Username/Email** | `harsh.kumar01@pinelabs.com` |
| **Password/API Token** | `[your NEW Atlassian API token]` |

> **Get a new API token here:** https://id.atlassian.com/manage-profile/security/api-tokens
> Click "Create API token" → Label: `agentverse` → Copy the token

4. Click **Register**
5. Click **Test** → should show green "OK · ~200ms"

### Via API (alternative)

```bash
# Generate base64 of email:token
ENCODED=$(echo -n "harsh.kumar01@pinelabs.com:YOUR_NEW_TOKEN" | base64)

curl -s -X POST http://localhost:8000/connectors \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"pinelabs-jira\",
    \"url\": \"https://pinelabs.atlassian.net\",
    \"auth_type\": \"custom_header\",
    \"auth_config\": {
      \"Authorization\": \"Basic ${ENCODED}\"
    }
  }" | python3 -m json.tool

# Save the server_id from response
export JIRA_CONNECTOR_ID="<server_id from response>"
```

### Verify you have project access

```bash
# Test that your token can see JIRA projects
curl -s -u "harsh.kumar01@pinelabs.com:YOUR_NEW_TOKEN" \
  "https://pinelabs.atlassian.net/rest/api/3/project/search" \
  -H "Accept: application/json" | python3 -m json.tool | grep '"key"'
```

**If you get `"total": 0`** — your account needs to be added to a JIRA project by your admin.
Ask your JIRA admin: *"Please add harsh.kumar01@pinelabs.com to the [PROJECT] project with Browse + Edit permissions"*

---

## Step 1.2 — Create the JIRA Agent

### Via UI (Recommended — uses AI Builder)

1. Go to **http://localhost:5173/agents** → **Create Agent**
2. Select **AI Builder** tab
3. Type: `"Create a JIRA triage agent for PineLabs project that reads tickets, assigns priority, and adds summary comments"`
4. Click **Generate** → reviews and creates the config
5. Click **Create Agent**

### Via API (Full control)

```bash
curl -s -X POST http://localhost:8000/agents \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "JIRA Triage Agent",
    "autonomy_mode": "supervised",
    "goal_template": "Perform JIRA operations on project {project_key}: {task}",
    "description": "Reads and manages JIRA tickets for PineLabs",
    "connector_ids": ["'"$JIRA_CONNECTOR_ID"'"]
  }' | python3 -m json.tool

export JIRA_AGENT_ID="<agent_id from response>"
```

**Autonomy mode explanation:**
```
supervised        → PAUSES and asks YOUR approval for every write operation
                   ✅ Use this first — safe, you stay in control

bounded-autonomous → Runs freely, logs everything, no pauses
                   ✅ Use after you trust the agent logic

fully-autonomous  → No human gates at all
                   ⚠️ Only use for proven, non-destructive tasks
```

---

## Step 1.3 — Submit Your First Goals (Read-Only)

Start safe — read operations only, zero risk.

### Goal 1: List open tickets

```bash
curl -s -X POST http://localhost:8000/goals \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Search JIRA for all open tickets in project PLAT with priority High. Return a formatted list with: ticket key, summary, assignee, days since created.",
    "agent_id": "'"$JIRA_AGENT_ID"'"
  }' | python3 -m json.tool

# Get the goal_id from response, then watch it execute:
curl -N -H "X-API-Key: $AV_KEY" \
  "http://localhost:8000/goals/{goal_id}/stream"
```

**What happens internally:**
```
[PLAN]    Agent decides: use jira_search_issues with JQL
[EXECUTE] Calls: jira_search_issues(jql="project=PLAT AND status=Open AND priority=High ORDER BY created DESC")
[VERIFY]  Gets back N tickets → "Goal achieved: found X high-priority open tickets"
[RESULT]  Returns formatted table
```

### Goal 2: Sprint summary

```bash
curl -s -X POST http://localhost:8000/goals \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Get the current active sprint for project PLAT board. List all tickets with their status. Count: total tickets, done, in-progress, to-do. Calculate completion percentage.",
    "agent_id": "'"$JIRA_AGENT_ID"'"
  }'
```

### Goal 3: Unestimated tickets

```bash
curl -s -X POST http://localhost:8000/goals \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Find all tickets in project PLAT that have no story points set and are not Done. Return them grouped by assignee.",
    "agent_id": "'"$JIRA_AGENT_ID"'"
  }'
```

### View execution live on frontend

Go to **http://localhost:5173/goals** → click on your goal → watch the real-time execution timeline with streaming tokens.

---

## Step 1.4 — First Write Operation (with HITL approval)

Since you're in `supervised` mode, writing pauses for your approval.

```bash
# Submit a write goal
curl -s -X POST http://localhost:8000/goals \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Create a new bug ticket in project PLAT. Summary: Login page crashes on mobile Safari iOS 17. Priority: High. Labels: frontend, mobile, safari. Description: Users report the login button becomes unresponsive after typing in the password field on Safari iOS 17.",
    "agent_id": "'"$JIRA_AGENT_ID"'"
  }'
```

**The agent pauses** → goes to **http://localhost:5173/approvals**

You see:
```
⚠️ Agent wants to call: jira_create_issue
   Project: PLAT
   Summary: Login page crashes on mobile Safari iOS 17
   Priority: High

[Approve] [Reject]
```

Click **Approve** → agent creates the ticket → returns the new issue key (e.g., `PLAT-456`)

**Or approve via API:**
```bash
# Get the pending approval
curl -s http://localhost:8000/governance/approvals \
  -H "X-API-Key: $AV_KEY" | python3 -m json.tool

# Approve it
curl -s -X POST http://localhost:8000/governance/approvals/{request_id}/approve \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{"approver": "harsh.kumar01@pinelabs.com", "note": "Looks correct, approved"}'
```

---

## Step 1.5 — Ghost Run (Preview Before Executing)

Before any bulk operation, preview what the agent WOULD do:

```bash
curl -s -X POST http://localhost:8000/goals \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Find all tickets in PLAT with status Done that are older than 90 days and transition them to Closed",
    "agent_id": "'"$JIRA_AGENT_ID"'",
    "dry_run": true
  }'
```

Shows the plan (what JIRA calls it would make) **without executing anything**.
Or via UI: **http://localhost:5173/goals/ghost-run**

---

## Step 1.6 — Upgrade to Bounded-Autonomous

Once you trust the agent, remove the approval gates:

```bash
curl -s -X PUT http://localhost:8000/agents/$JIRA_AGENT_ID \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{"autonomy_mode": "bounded-autonomous"}'
```

Now set up governance policies to protect critical operations:

```bash
# Block all deletes forever
curl -s -X POST http://localhost:8000/governance/policies \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Block JIRA deletes",
    "rule": "jira_delete_*",
    "action": "DENY",
    "enabled": true
  }'

# Require approval for P0 ticket creation
curl -s -X POST http://localhost:8000/governance/policies \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "P0 ticket approval required",
    "rule": "jira_create_issue",
    "action": "REQUIRE_APPROVAL",
    "time_window": null,
    "enabled": true
  }'
```

---

## Step 1.7 — Set Up a Daily Schedule

Automate the morning standup prep:

```bash
# Natural language schedule
curl -s -X POST http://localhost:8000/nl/schedule \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "command": "Every weekday at 9 AM, run the JIRA standup prep goal"
  }' | python3 -m json.tool

# Then set the goal template on the schedule
```

Or via UI: **http://localhost:5173/schedules** → **NL Scheduler** tab → type:
`"Every weekday morning at 9 AM: get all tickets updated yesterday in project PLAT"`

---

## Phase 1 Checklist

- [ ] JIRA connector registered and tests as "OK"
- [ ] JIRA agent created in supervised mode
- [ ] Ran 3+ read goals successfully (search, sprint, unestimated)
- [ ] Ran 1 write goal and approved via HITL
- [ ] Tried ghost run to preview bulk operation
- [ ] Upgraded to bounded-autonomous mode
- [ ] Set up at least 2 governance policies (DENY delete, REQUIRE_APPROVAL for P0)
- [ ] Set up 1 scheduled goal

---

# PHASE 2: Multi-Connector Agent
**Time to complete: 1-2 hours**
**What you build: Agent spanning JIRA + GitHub + Slack — syncing code, tickets, and notifications**

---

## Step 2.1 — Register GitHub Connector

```bash
# GitHub: uses Bearer token (Personal Access Token)
# Get your PAT at: github.com → Settings → Developer settings → Personal access tokens
curl -s -X POST http://localhost:8000/connectors \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "pinelabs-github",
    "url": "https://api.github.com",
    "auth_type": "bearer",
    "auth_config": {"token": "ghp_your_github_token_here"},
    "description": "PineLabs GitHub organization"
  }' | python3 -m json.tool

export GITHUB_CONNECTOR_ID="<server_id>"
```

**GitHub PAT scopes needed:** `repo`, `read:org`, `read:user`

---

## Step 2.2 — Register Slack Connector

```bash
# Slack: uses Bearer (Bot User OAuth Token starting with xoxb-)
# Get it at: api.slack.com/apps → your app → OAuth & Permissions → Bot User OAuth Token
curl -s -X POST http://localhost:8000/connectors \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "pinelabs-slack",
    "url": "https://slack.com/api",
    "auth_type": "bearer",
    "auth_config": {"token": "xoxb-your-slack-bot-token"},
    "description": "PineLabs engineering Slack workspace"
  }' | python3 -m json.tool

export SLACK_CONNECTOR_ID="<server_id>"
```

**Slack bot scopes needed:** `chat:write`, `channels:read`, `channels:history`, `search:read`

---

## Step 2.3 — Create Multi-Connector Agent

```bash
curl -s -X POST http://localhost:8000/agents \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Engineering Sync Agent",
    "autonomy_mode": "bounded-autonomous",
    "goal_template": "Sync engineering data across JIRA, GitHub, and Slack: {task}",
    "description": "Cross-system agent: JIRA tickets ↔ GitHub PRs ↔ Slack notifications",
    "connector_ids": [
      "'"$JIRA_CONNECTOR_ID"'",
      "'"$GITHUB_CONNECTOR_ID"'",
      "'"$SLACK_CONNECTOR_ID"'"
    ]
  }' | python3 -m json.tool

export SYNC_AGENT_ID="<agent_id>"
```

**How the agent picks the right connector:**
When you submit a goal, the planning LLM sees ALL available tools:
```
Available tools:
  JIRA:   jira_search_issues, jira_create_issue, jira_update_issue, jira_add_comment, jira_transition_issue
  GitHub: github_list_repos, github_list_issues, github_create_issue, github_create_pr, github_get_file
  Slack:  slack_send_message, slack_list_channels, slack_get_channel_history
```
It plans which tools to call in what order.

---

## Step 2.4 — Multi-Connector Goals

### Goal: PR → JIRA sync

```bash
curl -s -X POST http://localhost:8000/goals \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Get all pull requests merged to main branch in the pinelabs/backend repo in the last 24 hours. For each PR: 1) Find if there is a linked JIRA ticket mentioned in the PR title or description (format: PLAT-XXX). 2) If found, add a comment on the JIRA ticket: PR merged: [PR title] - [PR URL]. 3) If no JIRA ticket found, create a new JIRA task with the PR title as summary.",
    "agent_id": "'"$SYNC_AGENT_ID"'"
  }'
```

**What happens (parallel execution):**
```
Wave 1: github_list_pull_requests(repo="pinelabs/backend", state="closed", since="24h ago")
         → Returns [PR#123, PR#124, PR#125]

Wave 2 (parallel for each PR):
  ├── PR#123: jira_search_issues(jql="text ~ 'PLAT-100'") → found PLAT-100
  │           jira_add_comment(PLAT-100, "PR merged: Fix login bug - https://...")
  ├── PR#124: jira_search_issues(jql="text ~ 'PLAT-'") → not found
  │           jira_create_issue(summary="Add dark mode toggle", type=Task)
  └── PR#125: jira_search_issues(...) → found PLAT-102
              jira_add_comment(PLAT-102, "PR merged: ...")
```

### Goal: Weekly engineering report

```bash
curl -s -X POST http://localhost:8000/goals \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Generate the weekly engineering report: 1) Get all JIRA tickets completed this week in project PLAT (status transitioned to Done). 2) Get all PRs merged to main this week in pinelabs/backend. 3) Calculate: total story points delivered, number of bugs fixed, number of features shipped. 4) Post a formatted summary to Slack channel #engineering-weekly. Format: bold headers, bullet points, include ticket/PR links.",
    "agent_id": "'"$SYNC_AGENT_ID"'"
  }'
```

### Goal: Stale PR alert

```bash
curl -s -X POST http://localhost:8000/goals \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Find all open GitHub PRs in pinelabs/backend that have been waiting for review for more than 3 days. For each stale PR: 1) Find the linked JIRA ticket. 2) Update the JIRA ticket comment: PR stale since [date], needs review. 3) Send a Slack DM to the PR author reminding them to ping reviewers.",
    "agent_id": "'"$SYNC_AGENT_ID"'"
  }'
```

---

## Step 2.5 — Schedule Multi-Connector Automation

```bash
# Monday morning sync
curl -s -X POST http://localhost:8000/schedules \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Weekly Engineering Report",
    "cron": "0 9 * * 1",
    "goal_template": "Generate weekly engineering report: JIRA completions + GitHub PRs + post to Slack #engineering-weekly",
    "agent_id": "'"$SYNC_AGENT_ID"'",
    "enabled": true
  }'

# Daily EOD sync
curl -s -X POST http://localhost:8000/schedules \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Daily PR-JIRA Sync",
    "cron": "0 18 * * 1-5",
    "goal_template": "Sync all PRs merged today in pinelabs/backend to their corresponding JIRA tickets. Post daily summary to #dev-updates",
    "agent_id": "'"$SYNC_AGENT_ID"'",
    "enabled": true
  }'
```

---

## Phase 2 Checklist

- [ ] GitHub connector registered and tested
- [ ] Slack connector registered and tested
- [ ] Multi-connector agent created with all 3 connectors
- [ ] Ran PR→JIRA sync goal successfully
- [ ] Ran weekly report goal and saw Slack message
- [ ] Set up 2 scheduled automations
- [ ] Verified parallel wave execution in goal detail timeline

---

# PHASE 3: Multi-Agent Patterns
**Time to complete: 2-4 hours**
**What you build: Hierarchy of specialized agents + Workflow Builder automation**

---

## Step 3.1 — Create Specialist Agents

Each agent has ONE job and ONE connector set.

```bash
# Agent 1: JIRA Specialist (reads + writes tickets)
curl -s -X POST http://localhost:8000/agents \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "JIRA Specialist",
    "autonomy_mode": "bounded-autonomous",
    "goal_template": "JIRA operation: {task}. Project: {project_key}",
    "connector_ids": ["'"$JIRA_CONNECTOR_ID"'"]
  }' | python3 -m json.tool
export JIRA_SPECIALIST_ID="<agent_id>"

# Agent 2: GitHub Specialist (reads PRs, code, issues)
curl -s -X POST http://localhost:8000/agents \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "GitHub Specialist",
    "autonomy_mode": "bounded-autonomous",
    "goal_template": "GitHub operation: {task}. Repo: {repo}",
    "connector_ids": ["'"$GITHUB_CONNECTOR_ID"'"]
  }' | python3 -m json.tool
export GITHUB_SPECIALIST_ID="<agent_id>"

# Agent 3: Slack Notifier (sends messages and reads channel history)
curl -s -X POST http://localhost:8000/agents \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Slack Notifier",
    "autonomy_mode": "fully-autonomous",
    "goal_template": "Send Slack notification: {message}. Channel: {channel}",
    "connector_ids": ["'"$SLACK_CONNECTOR_ID"'"]
  }' | python3 -m json.tool
export SLACK_SPECIALIST_ID="<agent_id>"
```

---

## Step 3.2 — Supervisor Pattern

One coordinator decomposes a complex goal into sub-tasks and delegates to specialists.

```bash
curl -s -X POST http://localhost:8000/goals \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Prepare complete end-of-sprint report for PLAT Sprint 23: Get all completed JIRA tickets with story points, find all merged PRs this sprint with their authors, calculate velocity vs planned, identify top 3 blockers from tickets that were moved out of sprint, and post the full report to Slack #sprint-reviews with charts and links",
    "workflow_mode": "supervisor"
  }'
```

**The Supervisor Agent decomposes this into:**
```
Supervisor creates 4 sub-goals in parallel:

Sub-goal A → JIRA Specialist:
  "Get all PLAT tickets with status Done in Sprint 23. Include: key, summary, assignee, story points, completion date"

Sub-goal B → GitHub Specialist:
  "List all PRs merged to pinelabs/backend tagged with Sprint 23 or merged during Sprint 23 dates. Include author, title, URL, lines changed"

Sub-goal C → JIRA Specialist (after A):
  "Find all PLAT tickets that were in Sprint 23 but got moved to backlog. Identify the reasons from comments"

Sub-goal D → Slack Notifier (after A+B+C):
  "Post formatted sprint report to #sprint-reviews with: velocity=42pts/planned=50pts, 15 tickets done, 8 PRs merged, 3 carried-over tickets. Include all links."

Supervisor synthesizes results → "Sprint 23 report posted to #sprint-reviews"
```

---

## Step 3.3 — Debate Mode (For Important Decisions)

Use when you need confidence in a decision before acting.

```bash
curl -s -X POST http://localhost:8000/goals \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "JIRA ticket PLAT-500 title: Payment service down - affecting 20% of transactions. Current priority: Medium. Based on the ticket description, comments, and the fact that 20% of transactions are affected, should this be escalated to P0? Provide recommendation with reasoning and what immediate actions should be taken.",
    "debate_mode": true,
    "debate_agents": 3
  }'
```

**What happens:**
```
Round 1 — 3 independent agents each analyze the ticket:
  Agent 1: "YES - P0. Payment failures are revenue-impacting. 20% threshold exceeds P0 criteria."
  Agent 2: "YES - P0. Immediate engineering escalation needed. SLA breach risk."
  Agent 3: "YES - P0, but verify if 20% is of all transactions or just a segment first."

Round 2 — Each critiques the others:
  Agent 1 critiques Agent 3: "Verification can happen after escalation, not instead of it"
  Agent 2 agrees with Agent 1
  Agent 3 updates: "Agree on P0 escalation"

Round 3 — Vote:
  All 3 vote for immediate P0 escalation
  Consensus: 1.0 (unanimous)

Result: "ESCALATE TO P0 immediately. Actions: 1) Page on-call engineer, 2) Update PLAT-500 priority to P0..."
```

**When to use debate mode:**
- Production incident severity assessment
- Architecture decision (microservice vs monolith)
- Whether to skip a release due to a bug
- Budget impact analysis
- Any decision where being wrong is expensive

---

## Step 3.4 — Visual Workflow Builder

Build a recurring automation as a visual workflow.

1. Go to **http://localhost:5173/workflow-builder**
2. Click **"Generate from NL"**
3. Type: `"Every Monday: Get unestimated JIRA tickets, search similar historical tickets to estimate story points, update each ticket, notify the team on Slack"`

**The canvas generates:**

```
[Trigger: Monday 9 AM]
         ↓
[JIRA Tool: jira_search_issues]
 jql: "project=PLAT AND story_points is EMPTY AND status != Done"
         ↓
[Decision: tickets found?] ──No──→ [Slack: "No unestimated tickets 🎉"] → [End]
         ↓ Yes
[Parallel Fan-out] ─────────────────────────────────────────────────┐
    ↓                      ↓                          ↓             │
[JIRA: search            [JIRA: search             [JIRA: search    │
 similar to ticket 1]     similar to ticket 2]      similar to T3]  │
    ↓                      ↓                          ↓             │
    └──────────────── [Agent Step: Calculate estimates] ────────────┘
                                   ↓
                    [Loop: For each ticket]
                           ↓
                    [JIRA: jira_update_issue(story_points=estimate)]
                           ↓
                    [Slack: Post summary to #dev-team]
                           ↓
                         [End]
```

4. **Save** the workflow → name it "Weekly Estimation Bot"
5. **Schedule** it: set trigger to cron `0 9 * * 1`
6. Click **Test Run (Dry)** to verify logic without executing

---

## Phase 3 Checklist

- [ ] 3 specialist agents created (JIRA, GitHub, Slack)
- [ ] Ran supervisor-mode sprint report goal
- [ ] Used debate mode for a real decision (e.g., ticket severity)
- [ ] Built workflow in Workflow Builder via NL-generate
- [ ] Connected workflow to a Monday 9 AM schedule
- [ ] Tested workflow with dry run

---

# PHASE 4: Agent Civilization
**Time to complete: 1 day setup, then autonomous**
**What you build: A self-governing society of agents that creates, manages, and improves agents autonomously**

---

## Understanding the Civilization Architecture

```
                    ┌─────────────────────────────────┐
                    │       CivilizationOrchestrator   │
                    │                                  │
                    │  ┌──────────┐  ┌─────────────┐  │
                    │  │ Governor │  │ Constitution │  │
                    │  │(spawn/   │  │(hard rules   │  │
                    │  │ retire)  │  │ never broken)│  │
                    │  └──────────┘  └─────────────┘  │
                    │                                  │
                    │  ┌─────────────────────────────┐ │
                    │  │         Blackboard           │ │
                    │  │  Shared knowledge all agents │ │
                    │  │  read/write here             │ │
                    │  └─────────────────────────────┘ │
                    │                                  │
                    │  ┌──────────────────────────┐   │
                    │  │     LearningPipeline      │   │
                    │  │  Extracts winning patterns │   │
                    │  │  Updates agent prompts     │   │
                    │  └──────────────────────────┘   │
                    │                                  │
                    │  Agents: [A1] [A2] [A3] [A4...]  │
                    └─────────────────────────────────┘

Every 30 seconds (civilization tick):
  1. Observe  → Each agent reports status to Blackboard
  2. Evaluate → Governor scores: success_rate, queue_depth, last_used
  3. Decide   → Spawn new? Retire poor? Reassign?
  4. Act      → Execute decisions
  5. Learn    → Extract patterns from this tick
  6. Propagate→ Broadcast learnings to all agents
```

---

## Step 4.1 — Create the Civilization

```bash
curl -s -X POST http://localhost:8000/civilization \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "PineLabs Engineering Ops",
    "description": "Autonomous management of JIRA, GitHub, and Slack for the engineering organization",
    "constitution": {
      "rules": [
        "Never delete any JIRA ticket, GitHub PR, or Slack message — only close or archive",
        "Never create P0 or P1 tickets without human approval",
        "Never post to public Slack channels without explicit instruction",
        "Maximum 50 JIRA API calls per minute across all agents combined",
        "All ticket updates must include [AgentVerse] prefix in comments so humans can identify them",
        "If an agent fails 3 consecutive goals, pause it and alert the human"
      ],
      "max_agents": 5,
      "spawn_threshold": 0.65,
      "retire_threshold": 0.35
    },
    "seed_agents": [
      {
        "name": "JIRA Triage Bot v1",
        "autonomy_mode": "bounded-autonomous",
        "goal_template": "Triage new JIRA tickets: assign priority, add labels, link related issues",
        "connector_ids": ["'"$JIRA_CONNECTOR_ID"'"]
      }
    ],
    "connectors": [
      "'"$JIRA_CONNECTOR_ID"'",
      "'"$GITHUB_CONNECTOR_ID"'",
      "'"$SLACK_CONNECTOR_ID"'"
    ]
  }' | python3 -m json.tool

export CIV_ID="<civilization_id>"
```

---

## Step 4.2 — Configure the Constitution (Hard Rules)

The Constitution is enforced by the Governor. **These rules can NEVER be overridden by any agent.**

```bash
curl -s -X PUT http://localhost:8000/civilization/$CIV_ID/constitution \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "rules": [
      {
        "id": "no-delete",
        "description": "No agent may delete data",
        "action_pattern": "*.delete_*",
        "enforcement": "hard_block"
      },
      {
        "id": "p0-human",
        "description": "P0/P1 ticket creation requires human approval",
        "condition": "priority in [Highest, Critical, P0, P1]",
        "enforcement": "require_approval"
      },
      {
        "id": "rate-limit",
        "description": "Max 50 JIRA calls/minute",
        "enforcement": "rate_limit",
        "calls_per_minute": 50
      },
      {
        "id": "comment-prefix",
        "description": "All comments must start with [AgentVerse]",
        "enforcement": "transform",
        "transform": "prepend_[AgentVerse]_to_body"
      }
    ]
  }'
```

---

## Step 4.3 — Add Civilization Schedules

The civilization runs these goals on schedules, managed by the Governor:

```bash
# Morning standup prep — every weekday
curl -s -X POST http://localhost:8000/schedules \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Daily Standup Prep",
    "cron": "0 9 * * 1-5",
    "goal_template": "Prepare standup report: list all JIRA tickets updated yesterday in PLAT, identify blockers, calculate in-progress count. Post to Slack #daily-standup",
    "civilization_id": "'"$CIV_ID"'",
    "enabled": true
  }'

# PR review reminders — twice daily
curl -s -X POST http://localhost:8000/schedules \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "PR Review Reminders",
    "cron": "0 11,16 * * 1-5",
    "goal_template": "Find all open PRs in pinelabs/backend awaiting review for more than 4 hours. For each: notify the reviewer on Slack and add a comment on the linked JIRA ticket",
    "civilization_id": "'"$CIV_ID"'",
    "enabled": true
  }'

# Weekly sprint health — Friday afternoon
curl -s -X POST http://localhost:8000/schedules \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Sprint Health Check",
    "cron": "0 16 * * 5",
    "goal_template": "Assess sprint health for PLAT: compare planned vs actual story points, identify risk items, generate next week prioritization suggestions. Post to Slack #eng-leadership",
    "civilization_id": "'"$CIV_ID"'",
    "enabled": true
  }'
```

---

## Step 4.4 — Watch the Governor in Action

Monitor your civilization over the first week:

```bash
# Check civilization status
curl -s http://localhost:8000/civilization/$CIV_ID \
  -H "X-API-Key: $AV_KEY" | python3 -m json.tool

# See all active agents + their scores
curl -s http://localhost:8000/civilization/$CIV_ID/agents \
  -H "X-API-Key: $AV_KEY" | python3 -m json.tool

# See what's on the shared Blackboard
curl -s http://localhost:8000/civilization/$CIV_ID/blackboard \
  -H "X-API-Key: $AV_KEY" | python3 -m json.tool

# See what the civilization has learned
curl -s http://localhost:8000/civilization/$CIV_ID/learnings \
  -H "X-API-Key: $AV_KEY" | python3 -m json.tool
```

**Via UI:** http://localhost:5173/civilization → see orbit view of agents + live metrics

---

## Step 4.5 — Governor's Spawn/Retire Algorithm

The Governor runs this logic every 30 seconds:

```python
# SPAWN decision:
if (
    task_queue_depth > 5          # More than 5 goals queued
    AND all_agents_busy           # No idle agents
    AND best_agent.success_rate > 0.65  # They ARE capable, just overloaded
    AND active_agents < constitution.max_agents  # Not at max
    AND constitution.spawn_threshold_met
):
    new_agent = clone_from(best_performing_agent)
    new_agent.inherit_learnings = True  # Gets all learned patterns
    civilization.spawn(new_agent)

# RETIRE decision:
if (
    agent.success_rate < constitution.retire_threshold  # Below 35%
    AND agent.last_used_minutes_ago > 30  # Not actively working
    AND civilization.active_count > 1  # Never retire the last agent
):
    civilization.retire(agent)
    # Agent's learnings are preserved in the LearningPipeline before retirement
```

**What you'll see over time:**
```
Day 1:  1 agent (JIRA Triage Bot v1)  → handles 30 goals/day, success_rate: 0.74

Day 2:  Queue depth hits 8 at 11 AM
        Governor spawns: JIRA Triage Bot v2 (clone of v1, inherits all learnings)
        Now: 2 agents, 60 goals/day

Day 3:  GitHub PRs piling up in goals
        Governor spawns: GitHub Sync Bot (new specialty, uses GitHub connector)
        Now: 3 agents, covering JIRA + GitHub

Day 5:  JIRA Triage Bot v2 success_rate drops to 0.31
        Governor investigates: v2 is failing on edge cases v1 handles
        LearningPipeline: extracts v1's winning patterns
        Governor upgrades v2 prompt → success_rate recovers to 0.78

Day 7:  JIRA Triage Bot v2 success_rate 0.28 (still struggling)
        Governor retires v2
        Spawns v3 with improved prompt from v1's learnings

Day 14: Stable at 3 agents: JIRA Specialist, GitHub Sync, Slack Broadcaster
        success_rates: 0.91, 0.87, 0.94
        Self-improvement experiments running: 2 active A/B tests on prompts
```

---

## Step 4.6 — Self-Improvement Monitoring

The civilization automatically runs A/B tests on agent prompts:

Go to **http://localhost:5173/self-improvement** to see:

```
Active Experiments:
  ┌─────────────────────────────────────────────────────────┐
  │ Experiment: JIRA Triage Prompt v1 vs v2                 │
  │ Status: Running (day 3 of 7)                            │
  │ Control (v1):  success_rate=0.74, n=23 goals           │
  │ Challenger (v2): success_rate=0.81, n=21 goals         │
  │ Statistical significance: 67% (need 95% to promote)    │
  │ Estimated: 4 more days to conclusion                    │
  └─────────────────────────────────────────────────────────┘

Recent improvements applied:
  ✅ "Ticket priority detection": +12% accuracy (promoted Day 5)
  ✅ "JQL query optimization": -34% API calls (promoted Day 3)
  ✅ "Sprint detection pattern": +8% sprint identification (promoted Day 7)
```

---

## Step 4.7 — Emergency Controls

```bash
# PAUSE all civilization activity (safe mode)
curl -s -X POST http://localhost:8000/civilization/$CIV_ID/control \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{"action": "pause"}'

# RESUME
curl -s -X POST http://localhost:8000/civilization/$CIV_ID/control \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{"action": "resume"}'

# NUCLEAR OPTION — stops ALL goals across entire platform
curl -s -X POST http://localhost:8000/governance/emergency-stop \
  -H "X-API-Key: $AV_KEY"

# Clear the emergency stop
curl -s -X DELETE http://localhost:8000/governance/emergency-stop \
  -H "X-API-Key: $AV_KEY"
```

---

## Phase 4 Checklist

- [ ] Civilization created with Constitution rules
- [ ] Seed agent running successfully
- [ ] 2+ scheduled goals configured
- [ ] Watched Governor spawn a second agent (happens when queue builds)
- [ ] Reviewed Blackboard shared state
- [ ] Reviewed LearningPipeline outputs
- [ ] Watched a Self-Improvement experiment run
- [ ] Tested emergency pause/resume

---

# Complete API Quick Reference

```bash
export AV_KEY="av_free_CvkhCJyL3OSJy6mWUe_2hpZCotGWYFYvzhg0xs-K-WQ"
export JIRA_CONNECTOR_ID="4cffee6d2b2b4928b8a90f45839b1a6c"   # Already registered
export JIRA_AGENT_ID="9644350a84c2411093b53d1732ccdb42"         # Already created

# CONNECTORS
GET    /connectors                    # List all
POST   /connectors                    # Register new
PUT    /connectors/:id                # Update
POST   /connectors/:id/test           # Test connectivity
DELETE /connectors/:id                # Remove

# AGENTS
GET    /agents                        # List all
POST   /agents                        # Create (manual config)
POST   /agents/create                 # Create (NL AI Builder)
GET    /agents/:id                    # Get details
PUT    /agents/:id                    # Update (change mode etc)
POST   /agents/:id/snapshot           # Save a version
POST   /agents/:id/rollback/:snap     # Rollback to version

# GOALS
POST   /goals                         # Submit goal
POST   /goals (dry_run:true)          # Ghost run — preview only
GET    /goals/:id                     # Status + result
GET    /goals/:id/stream              # Live SSE events (curl -N)
POST   /goals/:id/pause               # Pause
POST   /goals/:id/resume              # Resume
POST   /goals/:id/cancel              # Cancel

# GOVERNANCE (safety)
GET    /governance/approvals          # Pending HITL approvals
POST   /governance/approvals/:id/approve
POST   /governance/approvals/:id/reject
POST   /governance/policies           # Add policy rule (DENY/ALLOW/REQUIRE)
DELETE /governance/policies/:id       # Remove policy
POST   /governance/emergency-stop     # ⚠️ Kill switch — stops everything
DELETE /governance/emergency-stop     # Clear emergency stop

# SCHEDULES (automation)
POST   /schedules                     # Create cron schedule
POST   /nl/schedule                   # Create from English description
GET    /schedules                     # List active schedules
DELETE /schedules/:id                 # Delete
POST   /schedules/:id/pause           # Pause
POST   /schedules/:id/resume          # Resume

# CIVILIZATION
POST   /civilization                  # Create civilization
GET    /civilization/:id              # Status + metrics
GET    /civilization/:id/agents       # Active agents + scores
GET    /civilization/:id/blackboard   # Shared knowledge state
GET    /civilization/:id/learnings    # What it has learned
POST   /civilization/:id/control      # pause/resume/throttle
```

---

# Your Progression Timeline

```
Week 1: JIRA Agent (Phase 1)
  Day 1-2: Setup + first read goals
  Day 3-4: Write goals with HITL approval
  Day 5-7: Schedules + governance policies + bounded-autonomous

Week 2: Multi-Connector (Phase 2)
  Day 8-9: GitHub + Slack connectors
  Day 10-11: Cross-system goals (PR→JIRA sync)
  Day 12-14: Scheduled multi-connector workflows

Week 3: Multi-Agent (Phase 3)
  Day 15-16: Create specialist agents
  Day 17-18: Supervisor pattern for complex goals
  Day 19: Debate mode for decisions
  Day 20-21: Workflow Builder visual automation

Week 4+: Civilization (Phase 4)
  Day 22: Create civilization + constitution
  Day 23-24: Watch Governor spawn agents
  Day 25-28: Observe self-improvement experiments
  Day 29+: Civilization runs autonomously, you review weekly
```

---

*Guide saved at: `docs/agent-creation-guide.md`*
*Your AgentVerse API key: `av_free_CvkhCJyL3OSJy6mWUe_2hpZCotGWYFYvzhg0xs-K-WQ`*
*Your JIRA Connector ID: `4cffee6d2b2b4928b8a90f45839b1a6c`*
*Your JIRA Agent ID: `9644350a84c2411093b53d1732ccdb42`*
*Platform docs: `docs/features/` — 57 deep feature docs*
*Operations: `RUNBOOK.md`*
