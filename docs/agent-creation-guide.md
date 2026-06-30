# AgentVerse: Complete Agent Creation Guide
## From Simple JIRA Agent → Multi-Agent Civilizations

> **This is your end-to-end roadmap.** Start with a single JIRA agent, progress through multi-connector agents, multi-agent patterns, and arrive at fully autonomous Agent Civilizations that create and manage agents on their own.

---

## Table of Contents

1. [Platform Setup (Prerequisites)](#1-platform-setup)
2. [Phase 1 — Simple JIRA Agent](#2-phase-1--simple-jira-agent)
3. [Phase 2 — JIRA + GitHub + Slack (Multi-Connector)](#3-phase-2--multi-connector-agent)
4. [Phase 3 — Multi-Agent Patterns](#4-phase-3--multi-agent-patterns)
5. [Phase 4 — Autonomous Agent Civilization](#5-phase-4--agent-civilization)
6. [Agent Mastery Checklist](#6-agent-mastery-checklist)

---

## 1. Platform Setup

### 1.1 Start AgentVerse locally

```bash
# 1. Start Docker VM (macOS)
colima start

# 2. Start the minimum infrastructure
cd agent-verse-backend
docker-compose -f infra/docker-compose.yml up -d postgres redis

# 3. Apply database migrations
uv sync && uv run alembic upgrade head

# 4. Set your LLM API key in .env
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env
# OR
echo "OPENAI_API_KEY=sk-..." >> .env

# 5. Start the backend
uv run uvicorn app.main:app --reload --port 8000

# 6. Start Celery worker (needed for goal execution)
uv run celery -A app.scaling.celery_app worker \
  --loglevel=info -Q goals,schedules,maintenance --concurrency=2

# 7. Start the frontend (new terminal)
cd agent-verse-frontend && npm run dev
```

Open: **http://localhost:5173**

### 1.2 Create your first tenant

```bash
# Returns api_key — save this!
curl -s -X POST http://localhost:8000/tenants/signup \
  -H "Content-Type: application/json" \
  -d '{"name": "My Team", "email": "me@example.com"}' | python3 -m json.tool
```

**Or via UI:** Visit http://localhost:5173 → enter name + email → copy the API key shown.

Set your key for all subsequent `curl` commands:
```bash
export AV_KEY="av-your-api-key-here"
```

### 1.3 JIRA API credentials you need

Before creating a JIRA agent, get these from your Atlassian account:

| Credential | Where to get it |
|-----------|----------------|
| **JIRA Base URL** | `https://yourcompany.atlassian.net` |
| **JIRA Email** | Your Atlassian login email |
| **JIRA API Token** | https://id.atlassian.com → Security → Create API token |
| **Project Key** | From JIRA board URL: `jira.atlassian.net/jira/software/projects/PROJ/...` |

---

## 2. Phase 1 — Simple JIRA Agent

**Goal:** An agent that can read, create, and update JIRA tickets autonomously.

### Step 2.1 — Register the JIRA Connector

**Via UI:**
1. Go to **Connectors** (sidebar) → **Connector Catalog**
2. Find **JIRA** → click **Register**
3. Fill in:
   - Name: `My JIRA`
   - URL: `https://yourcompany.atlassian.net`
   - Auth Type: `api_key`
   - API Key: `your-email:your-api-token` (base64 encoded for Basic Auth) or just paste the token

**Via API:**
```bash
curl -s -X POST http://localhost:8000/connectors \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My JIRA",
    "url": "https://yourcompany.atlassian.net",
    "auth_type": "api_key",
    "auth_config": {
      "api_key": "your-api-token",
      "email": "you@yourcompany.com"
    },
    "description": "Main JIRA instance"
  }' | python3 -m json.tool
```

Copy the `server_id` from the response — you need it for the agent.

**Test the connector works:**
```bash
curl -s -X POST http://localhost:8000/connectors/{server_id}/test \
  -H "X-API-Key: $AV_KEY" | python3 -m json.tool
# Should show: "reachable": true
```

### Step 2.2 — Create the JIRA Agent

**Via UI:**
1. Go to **Agents** → **Create Agent**
2. Choose **AI Builder** tab
3. Type: `"Create a JIRA triage agent for project PROJ"`
4. Click **Generate** — the AI configures it automatically
5. Review the config → click **Create**

**Via API (full control):**
```bash
curl -s -X POST http://localhost:8000/agents \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "JIRA Triage Agent",
    "autonomy_mode": "supervised",
    "goal_template": "Triage all unassigned JIRA tickets in project {project_key}: categorize by priority, assign to appropriate team members, add labels, and post a summary comment on each.",
    "description": "Reads and triages JIRA tickets — assigns, labels, comments",
    "connector_ids": ["YOUR_JIRA_SERVER_ID"],
    "model": "claude-sonnet-4-5"
  }' | python3 -m json.tool
```

**Key fields explained:**

| Field | Value | Why |
|-------|-------|-----|
| `autonomy_mode` | `supervised` | Agent asks for human approval on write operations (safe start) |
| `goal_template` | NL description with `{variables}` | Reusable template; submit goals with specific values |
| `connector_ids` | `["jira-server-id"]` | Only this agent can call JIRA |
| `model` | `claude-sonnet-4-5` | Mid-tier model: good balance of cost/quality for tool use |

**Autonomy modes — when to use each:**

```
supervised           → Every high-risk step (create/update/delete) waits for YOUR approval
                      Use when: first time running, testing, prod deployments

bounded-autonomous   → Runs freely but logs every action for audit
                      Use when: you trust the agent logic, want speed, need audit trail

fully-autonomous     → Runs without any human gates
                      Use when: proven agent, repetitive well-defined task, non-destructive
```

### Step 2.3 — Submit Your First JIRA Goal

**Via UI:**
1. Go to **Goals** → **Submit Goal**
2. Select agent: `JIRA Triage Agent`
3. Type goal: `"List all open bugs in project PROJ with priority High, sorted by creation date"`
4. Click **Submit**
5. Watch the **Goal Detail** page — live execution timeline appears

**Via API:**
```bash
curl -s -X POST http://localhost:8000/goals \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "List all open bugs in project PROJ with priority High, sorted by creation date",
    "agent_id": "YOUR_AGENT_ID",
    "priority": "normal"
  }' | python3 -m json.tool
```

**Watch it execute (streaming events):**
```bash
curl -N -H "X-API-Key: $AV_KEY" \
  "http://localhost:8000/goals/{goal_id}/stream"
```

**What the agent does internally:**
```
1. PLAN:    "I need to search JIRA with JQL for high-priority open bugs"
2. EXECUTE: Calls jira_search_issues(jql="project=PROJ AND issuetype=Bug AND status=Open AND priority=High ORDER BY created DESC")
3. VERIFY:  "I got 12 issues back — the goal asked for a list, this is complete"
4. RESULT:  Returns formatted list of 12 bugs with summary, assignee, created date
```

### Step 2.4 — Try Write Operations (with HITL)

Since you're in `supervised` mode, write operations will pause for approval.

```bash
curl -s -X POST http://localhost:8000/goals \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Create a new bug ticket in PROJ: Summary=Login page crashes on mobile Safari, Priority=High, Labels=frontend,mobile",
    "agent_id": "YOUR_AGENT_ID"
  }' | python3 -m json.tool
```

The agent will:
1. Plan the `jira_create_issue` call
2. **PAUSE** → sends you a notification: "Agent wants to call `jira_create_issue` — approve?"
3. You approve at **http://localhost:5173/approvals**
4. Agent creates the ticket, returns the new issue key (e.g., `PROJ-456`)

**Approve via API:**
```bash
curl -s -X POST http://localhost:8000/governance/approvals/{request_id}/approve \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{"approver": "me@example.com", "note": "Looks good"}'
```

### Step 2.5 — Common JIRA Goals to Try

```bash
# 1. Sprint report
"Summarize all tickets completed in the last sprint for project PROJ. Include total story points, 
 number of bugs fixed, and top contributors by tickets closed."

# 2. Auto-triage
"Find all unassigned tickets in PROJ created in the last 7 days. For each one:
 - If it's a Bug: set priority to High, assign to the dev team lead
 - If it's a Task: add label 'needs-grooming'
 - Add a comment: 'Auto-triaged by AgentVerse on [today's date]'"

# 3. Stale ticket cleanup
"Find all tickets in PROJ that haven't been updated in 30+ days and are still In Progress.
 For each: add a comment asking the assignee for a status update."

# 4. Epic progress
"For epic PROJ-100, list all child stories, their statuses, and calculate % completion."

# 5. Release notes generation
"Generate release notes for version 2.0.0 of PROJ by finding all resolved tickets 
 since 2025-01-01 with fix version = 2.0.0. Group by Bug Fixes, Features, Improvements."
```

### Step 2.6 — Ghost Run First (Preview Before Executing)

Before running destructive operations, preview what the agent will do:

**Via UI:** Goals → Ghost Run  
**Via API:**
```bash
curl -s -X POST http://localhost:8000/goals \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Close all PROJ tickets with status Done that are older than 90 days",
    "agent_id": "YOUR_AGENT_ID",
    "dry_run": true
  }' | python3 -m json.tool
```

Shows you the plan (what JIRA calls it would make) without executing anything.

---

## 3. Phase 2 — Multi-Connector Agent

**Goal:** An agent that spans JIRA + GitHub + Slack — detecting code merged to main, creating JIRA tickets for any issues found, and notifying Slack.

### Step 3.1 — Register All 3 Connectors

```bash
# GitHub connector
curl -s -X POST http://localhost:8000/connectors \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "GitHub Org",
    "url": "https://api.github.com",
    "auth_type": "bearer",
    "auth_config": {"token": "ghp_your_token"},
    "description": "Main GitHub organization"
  }'

# Slack connector
curl -s -X POST http://localhost:8000/connectors \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Engineering Slack",
    "url": "https://slack.com/api",
    "auth_type": "bearer",
    "auth_config": {"token": "xoxb-your-bot-token"},
    "description": "Engineering workspace"
  }'
```

### Step 3.2 — Create a Multi-Connector Agent

```bash
curl -s -X POST http://localhost:8000/agents \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Release Sync Agent",
    "autonomy_mode": "bounded-autonomous",
    "goal_template": "Monitor the {repo} repository for new merged PRs, create JIRA tickets for any bug fixes or features, and post a Slack summary to #{channel}",
    "connector_ids": ["JIRA_ID", "GITHUB_ID", "SLACK_ID"],
    "description": "Syncs GitHub releases → JIRA tickets → Slack announcements"
  }'
```

### Step 3.3 — Multi-Connector Goal Examples

**Release notes pipeline:**
```
"Check GitHub repo 'myorg/backend' for all PRs merged today.
 For each PR:
   1. If it contains a bug fix (label=bug): create a JIRA ticket type=Bug in project PROJ
   2. If it contains a feature (label=feature): create a JIRA Story in PROJ
   3. Link the JIRA ticket to the PR URL
 Finally post a summary to Slack #releases channel:
   - Total PRs merged: N
   - Bugs fixed: N (with JIRA links)
   - Features shipped: N (with JIRA links)"
```

**Code review + ticket update:**
```
"Find all open PRs in 'myorg/backend' that have been waiting for review > 2 days.
 For each one:
   1. Find the linked JIRA ticket (from PR description)
   2. Add a comment on the JIRA ticket: 'PR waiting for review since [date]'
   3. Notify the JIRA assignee on Slack with a direct message"
```

**Sprint automation:**
```
"At the start of each sprint:
   1. Get the GitHub issues labeled 'sprint-ready' from 'myorg/backend'
   2. Create corresponding JIRA Stories in project PROJ with GitHub issue links
   3. Move all JIRA Stories to the new sprint
   4. Post the sprint plan to Slack #dev-team with a formatted table"
```

### Step 3.4 — How the Agent Routes Across Connectors

Internally, when the agent has 3 connectors, the planning LLM sees all available tools:
```
Available tools:
  - jira_search_issues, jira_create_issue, jira_update_issue, jira_add_comment
  - github_list_pull_requests, github_get_pull_request, github_create_issue
  - slack_send_message, slack_list_channels
```

The **ModelRouter** then:
- Uses `claude-opus-4-8` for **planning** (complex multi-step reasoning)
- Uses `claude-sonnet-4-5` for **executing** each step (calling individual tools)
- Uses `claude-haiku-3-5` for **verifying** (was the goal achieved?)

This cuts LLM cost by ~65% vs using the same model for everything.

### Step 3.5 — Parallel Execution (Wave-Based)

When you submit a goal with multiple independent steps, AgentVerse runs them **in parallel**:

```
Goal: "For projects PROJ1, PROJ2, PROJ3: get all open bugs and post summaries to Slack"

Wave 1 (parallel):
  ├── jira_search_issues(project=PROJ1)
  ├── jira_search_issues(project=PROJ2)
  └── jira_search_issues(project=PROJ3)

Wave 2 (all results available):
  └── slack_send_message(combined summary)
```

The `StructuredPlan.execution_waves()` algorithm automatically detects which steps can run in parallel based on data dependencies — you don't configure this manually.

### Step 3.6 — Governance for Write Operations

When your agent spans multiple connectors and performs writes, add policies:

**Via UI:** Governance → Policies → Add Policy

```json
{
  "name": "Require approval for Slack messages",
  "pattern": "slack.*",
  "action": "REQUIRE_APPROVAL",
  "time_window": null
}
```

```json
{
  "name": "Block production JIRA deletes",
  "pattern": "jira.delete_*",
  "action": "DENY"
}
```

```json
{
  "name": "Allow JIRA reads always",
  "pattern": "jira.get_*",
  "action": "ALLOW"
}
```

```json
{
  "name": "Restrict JIRA writes to business hours",
  "pattern": "jira.create_*",
  "action": "REQUIRE_APPROVAL",
  "time_window": {"start_hour": 9, "end_hour": 17, "weekdays": [0,1,2,3,4]}
}
```

---

## 4. Phase 3 — Multi-Agent Patterns

Now you build **systems of agents** where multiple specialized agents collaborate.

### Pattern A: Supervisor Agent (Orchestrator)

**Use case:** You have a complex goal that requires multiple specialists.

```
SupervisorAgent
    ├── JIRAAgent (reads/writes tickets)
    ├── GitHubAgent (reads PRs, code)
    ├── SlackAgent (sends notifications)
    └── AnalyticsAgent (generates reports)
```

**Create specialist agents first:**
```bash
# 1. JIRA Specialist
curl -s -X POST http://localhost:8000/agents -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "JIRA Specialist",
    "autonomy_mode": "bounded-autonomous",
    "goal_template": "Perform JIRA operations: {task}",
    "connector_ids": ["JIRA_CONNECTOR_ID"]
  }'

# 2. GitHub Specialist  
curl -s -X POST http://localhost:8000/agents -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "GitHub Specialist",
    "autonomy_mode": "bounded-autonomous",
    "goal_template": "Perform GitHub operations: {task}",
    "connector_ids": ["GITHUB_CONNECTOR_ID"]
  }'

# 3. Slack Specialist
curl -s -X POST http://localhost:8000/agents -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Slack Specialist",
    "autonomy_mode": "fully-autonomous",
    "goal_template": "Send Slack notifications: {message}",
    "connector_ids": ["SLACK_CONNECTOR_ID"]
  }'
```

**Submit a supervisor goal:**
```bash
curl -s -X POST http://localhost:8000/goals \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Generate our weekly engineering report: get all JIRA tickets closed this week, find the top 3 PRs merged, calculate team velocity, and post a formatted report to Slack #weekly-report",
    "workflow_mode": "supervisor"
  }'
```

**What the Supervisor does:**
```
1. Decomposes goal into 3 sub-goals:
   - Sub-goal A → JIRAAgent: "Get all tickets closed this week with assignee and story points"
   - Sub-goal B → GitHubAgent: "Find top 3 PRs by comment count and size merged this week"
   - Sub-goal C → (waits for A+B)
   
2. Runs A and B in parallel

3. When both complete:
   - Sub-goal C → SlackAgent: "Post formatted report with [A results] and [B results]"
   
4. Synthesizes: "Weekly report posted successfully to #weekly-report"
```

### Pattern B: Debate Mode (High-Stakes Decisions)

**Use case:** Before making an important decision (e.g., "should we escalate this bug to P0?"), have multiple agent instances debate and vote.

```bash
curl -s -X POST http://localhost:8000/goals \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Analyze PROJ-500: Login failure affecting 15% of users. Should this be escalated to P0? If yes, what immediate actions should be taken?",
    "debate_mode": true,
    "debate_agents": 3
  }'
```

**What happens:**
- 3 independent agent instances each analyze the ticket
- Round 1: Each proposes their assessment (escalate/don't + reasoning)
- Round 2: Each critiques the other two proposals
- Round 3: Confidence-weighted vote
- Winner: The proposal with highest vote count becomes the decision
- Result includes `consensus_level` (e.g., 0.67 = 2 of 3 agreed)

**When to use debate mode:**
- Architecture decisions
- Bug severity escalation
- Whether to break a deadline
- Risk assessment for a deployment
- Anything where being wrong has high cost

### Pattern C: Goal Tree (Recursive Decomposition)

**Use case:** A goal so large it needs to be split into sub-goals, each of which may split further.

```bash
curl -s -X POST http://localhost:8000/goals \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Perform a full sprint retrospective for PROJ Sprint 42: analyze all completed tickets, identify blockers that caused delays, suggest process improvements, create action items as JIRA tickets, and prepare a presentation summary",
    "workflow_mode": "goal_tree"
  }'
```

**The tree structure:**
```
Root: Sprint Retrospective
├── Branch 1: Data Collection (parallel)
│   ├── Leaf: Get all completed tickets in Sprint 42
│   ├── Leaf: Find tickets that moved out of sprint
│   └── Leaf: Get cycle time for each ticket
├── Branch 2: Analysis (depends on Branch 1)
│   ├── Leaf: Identify top 3 blockers by delay caused
│   └── Leaf: Calculate team velocity vs planned
├── Branch 3: Actions (depends on Branch 2)
│   ├── Leaf: Create JIRA improvement tickets
│   └── Leaf: Assign action items to team leads
└── Branch 4: Report (depends on Branch 3)
    └── Leaf: Post summary to Slack
```

### Pattern D: Multi-Agent Workflow Builder

**Use case:** Build a visual workflow that runs on a schedule.

1. Go to **Workflow Builder** (sidebar)
2. Click **Generate from NL**
3. Type: `"Every Monday morning: check JIRA for unestimated tickets, estimate them using historical data from similar tickets, update story points, and notify the scrum master on Slack"`
4. The canvas generates a workflow with nodes:

```
[Trigger: Monday 9 AM]
    ↓
[Tool Call: jira_search_issues(unestimated tickets)]
    ↓ 
[Decision: Found tickets?] → No → [End]
    ↓ Yes
[Parallel Fan-out]
    ├── [Tool Call: jira_search_issues(similar past tickets for T1)]
    ├── [Tool Call: jira_search_issues(similar past tickets for T2)]
    └── [Tool Call: jira_search_issues(similar past tickets for T3)]
    ↓
[Agent Step: Calculate story point estimates]
    ↓
[Loop: For each ticket]
    └── [Tool Call: jira_update_issue(story_points=estimated)]
    ↓
[Tool Call: slack_send_message(scrum master summary)]
    ↓
[End]
```

5. **Save** the workflow
6. **Schedule** it: set trigger to cron `0 9 * * 1` (Monday 9 AM)

---

## 5. Phase 4 — Agent Civilization

**This is where AgentVerse becomes truly autonomous.** A Civilization is a self-governing society of agents that:
- Creates new specialist agents when needed
- Retires agents that are no longer effective
- Learns from outcomes and improves agent prompts
- Coordinates through a shared Blackboard
- Governed by a Constitution (rules that cannot be overridden)

### 5.1 — Understanding the Civilization Architecture

```
CivilizationOrchestrator
    │
    ├── Governor          ← Evaluates spawning/killing decisions
    ├── Constitution      ← Immutable rules (e.g., "never delete production data")
    ├── Blackboard        ← Shared knowledge space (all agents read/write here)
    ├── Society           ← Registry of active agents
    ├── LearningPipeline  ← Extracts patterns from past executions
    └── Agents (N)        ← Active workers
```

**The civilization tick cycle (every 30 seconds):**
```
1. Observe  → Each agent reports its status to the Blackboard
2. Evaluate → Governor scores each agent: eval_score, success_rate, last_used
3. Decide   → Governor may: spawn new agent, retire poor agent, reassign load
4. Act      → Spawn/retire decisions execute via GoalService
5. Learn    → LearningPipeline extracts winning patterns from this tick
6. Propagate→ Learnings broadcast to all agents (update their prompts)
```

### 5.2 — Create Your First Civilization

**Via UI:** Enterprise → Civilization → New Civilization

**Via API:**
```bash
curl -s -X POST http://localhost:8000/civilization \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Engineering Ops Civilization",
    "description": "Autonomous management of our engineering workflows across JIRA, GitHub, and Slack",
    "constitution": {
      "rules": [
        "Never delete tickets, only close or archive them",
        "Always require human approval before creating issues with priority P0 or P1",
        "Never post to public Slack channels without explicit instruction",
        "Maximum 10 new tickets per hour to avoid flooding",
        "All actions must be logged to the audit trail"
      ],
      "max_agents": 10,
      "spawn_threshold": 0.6,
      "retire_threshold": 0.3
    },
    "seed_agents": [
      {
        "name": "JIRA Triage Bot",
        "autonomy_mode": "bounded-autonomous",
        "goal_template": "Triage new JIRA tickets in project {project}",
        "connector_ids": ["JIRA_ID"]
      }
    ],
    "connectors": ["JIRA_ID", "GITHUB_ID", "SLACK_ID"]
  }' | python3 -m json.tool
```

### 5.3 — How the Governor Spawns New Agents

The Governor uses this decision algorithm:

```python
# When does it spawn a new agent?
if (
    task_queue_depth > 5  # Backlog building up
    AND existing_agents_all_busy
    AND success_rate_of_existing > 0.6  # They're capable, just overloaded
    AND spawn_budget_available
):
    spawn_new_agent(
        name=f"JIRA Triage Bot #{n+1}",
        clone_from="JIRA Triage Bot",  # Copy the best-performing agent
        inherit_learnings=True
    )

# When does it retire an agent?
if (
    agent.success_rate < constitution.retire_threshold  # 30%
    AND agent.last_used > 2_hours_ago
    AND len(active_agents) > 1  # Never retire the last one
):
    retire_agent(agent_id)
```

### 5.4 — The Blackboard: Shared Agent Memory

Every agent in the civilization can read and write to the shared Blackboard:

```
Blackboard entries (examples):
  Key: "jira:project:PROJ:last_sprint_velocity"  Value: 42
  Key: "jira:ticket:PROJ-500:assigned_to"         Value: "alice@team.com"
  Key: "github:repo:backend:last_merge"           Value: "2026-06-30T09:00:00Z"
  Key: "slack:channel:eng-alerts:last_message"    Value: "Deployment successful"
  Key: "pattern:estimation:accuracy"              Value: 0.87
```

Agents use the Blackboard to avoid duplicating work:
- `JIRA Triage Bot #1` checks: "Has another agent already triaged PROJ-500?" 
- Blackboard says yes → skip it → move to next ticket

### 5.5 — Constitution Rules (Hard Limits)

The Constitution is the governance layer for the entire civilization. These rules can NEVER be overridden by any agent or goal:

```bash
curl -s -X PUT http://localhost:8000/civilization/{civ_id}/constitution \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "rules": [
      {
        "id": "no-delete",
        "rule": "No agent may delete any JIRA ticket, GitHub PR, or Slack message",
        "action_pattern": "*.delete_*",
        "enforcement": "hard_block"
      },
      {
        "id": "p0-human-approval",
        "rule": "Creating P0 or P1 JIRA tickets requires human approval",
        "condition": "issue_priority in [P0, P1]",
        "enforcement": "require_approval"
      },
      {
        "id": "rate-limit",
        "rule": "Maximum 50 JIRA API calls per minute across all agents",
        "enforcement": "rate_limit",
        "limit": 50,
        "window_seconds": 60
      }
    ]
  }'
```

### 5.6 — Learning Pipeline: Self-Improvement

The civilization automatically improves itself:

```
After each successful goal completion:
  1. EvalRunner scores it (6 dimensions)
  2. If score > 0.85:
     - Extract the winning plan as a pattern
     - Store in LongTermMemory
     - Propagate to all agents: "Next time you see a similar goal, here's the plan that worked"
  
After each failure:
  1. SelfOptimizer analyzes: what went wrong?
  2. Generates a suggestion: "Try adding more context about the project key to the prompt"
  3. Creates an A/B variant
  4. Mann-Whitney U test after 20 runs: if challenger wins at 95% confidence → promote to control
```

You can see this in **Self-Improvement** (sidebar) → shows active experiments and their results.

### 5.7 — Full Autonomous JIRA + GitHub Civilization Example

This civilization manages ALL engineering operations autonomously:

```bash
curl -s -X POST http://localhost:8000/civilization \
  -H "X-API-Key: $AV_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "FullStack Engineering Civ",
    "constitution": {
      "rules": [
        "Never delete production data",
        "P0 bugs require human approval before any action",
        "Maximum 3 agents active simultaneously"
      ],
      "max_agents": 3,
      "spawn_threshold": 0.65,
      "retire_threshold": 0.35
    },
    "seed_agents": [
      {
        "name": "JIRA Triage Specialist",
        "goal_template": "Continuously triage new JIRA tickets: assign priority, add labels, link to related issues",
        "connector_ids": ["JIRA_ID"],
        "autonomy_mode": "bounded-autonomous"
      }
    ],
    "schedules": [
      {
        "trigger": "cron:0 9 * * 1-5",
        "goal": "Morning standup prep: summarize yesterdays JIRA completions and todays priorities"
      },
      {
        "trigger": "cron:0 18 * * 5",
        "goal": "End-of-week report: sprint velocity, blockers encountered, next week planning"
      },
      {
        "trigger": "webhook:github_push",
        "goal": "On every GitHub push to main: check if any JIRA tickets are affected and update their status to In Review"
      }
    ]
  }' | python3 -m json.tool
```

**What happens over time:**
```
Day 1:  1 JIRA Triage Specialist running
        Handles: 45 tickets/day, success_rate: 0.72

Day 3:  Backlog growing (> 5 tickets/hour)
        Governor spawns JIRA Triage Specialist #2
        Now: 90 tickets/day, success_rate: 0.78

Day 5:  GitHub webhook firing → JIRA status updates needed
        Governor spawns: JIRA-GitHub Bridge Specialist
        Now: 3 agents, full coverage

Day 7:  Learning: "Tickets with no description get misrouted"
        SelfOptimizer adds to prompt: "Always check description before routing"
        Success_rate improves to 0.89

Day 14: JIRA Triage Specialist #2 has success_rate 0.71 (vs #1 at 0.91)
        Governor decides: retire #2, spawn a better one trained on #1's learnings
```

### 5.8 — Monitor Your Civilization

```bash
# Get civilization status
curl -s http://localhost:8000/civilization/{civ_id} -H "X-API-Key: $AV_KEY"

# Get active agents in the civ
curl -s http://localhost:8000/civilization/{civ_id}/agents -H "X-API-Key: $AV_KEY"

# Get blackboard state
curl -s http://localhost:8000/civilization/{civ_id}/blackboard -H "X-API-Key: $AV_KEY"

# Get learning history
curl -s http://localhost:8000/civilization/{civ_id}/learnings -H "X-API-Key: $AV_KEY"

# Pause the civilization (keep agents, stop new goals)
curl -s -X POST http://localhost:8000/civilization/{civ_id}/control \
  -H "X-API-Key: $AV_KEY" -d '{"action": "pause"}'

# Resume
curl -s -X POST http://localhost:8000/civilization/{civ_id}/control \
  -H "X-API-Key: $AV_KEY" -d '{"action": "resume"}'
```

**Via UI:** Enterprise → Civilization → your civ → live orbit view of agents + blackboard + metrics

---

## 6. Agent Mastery Checklist

### Phase 1: Simple Agent ✓
- [ ] Registered JIRA connector and tested it
- [ ] Created a supervised agent
- [ ] Submitted a read goal and watched live execution
- [ ] Submitted a write goal and approved via HITL
- [ ] Tried Ghost Run to preview before executing
- [ ] Ran at least 5 different JIRA goals

### Phase 2: Multi-Connector ✓
- [ ] Registered GitHub + Slack connectors
- [ ] Created agent with 3 connectors
- [ ] Executed a cross-system goal (JIRA → GitHub → Slack)
- [ ] Set up governance policies for write operations
- [ ] Switched agent to `bounded-autonomous` mode
- [ ] Set up a scheduled goal (e.g., Monday morning report)

### Phase 3: Multi-Agent ✓
- [ ] Created 3 specialist agents
- [ ] Submitted a supervisor-mode goal
- [ ] Tried debate mode for a high-stakes decision
- [ ] Built a workflow in the Workflow Builder
- [ ] Scheduled a recurring workflow
- [ ] Reviewed the Knowledge Base (add JIRA docs for better context)

### Phase 4: Civilization ✓
- [ ] Created a civilization with 1 seed agent
- [ ] Watched the Governor spawn a second agent automatically
- [ ] Configured the Constitution with 3+ rules
- [ ] Reviewed Blackboard activity
- [ ] Observed a Self-Improvement experiment
- [ ] Set up 2+ civilization-level schedules
- [ ] Monitored civilization metrics for 1 week

---

## Quick Reference: API Cheat Sheet

```bash
# === CONNECTORS ===
POST /connectors                   # Register new connector
GET  /connectors                   # List all
POST /connectors/{id}/test         # Test connectivity
GET  /connectors/catalog           # Browse 32 templates

# === AGENTS ===
POST /agents                       # Create agent (manual)
POST /agents/create                # Create agent (NL AI Builder)
GET  /agents                       # List all agents
GET  /agents/{id}                  # Get agent details
PUT  /agents/{id}                  # Update agent
POST /agents/{id}/snapshot         # Version the agent
POST /agents/{id}/rollback/{snap}  # Rollback to version

# === GOALS ===
POST /goals                        # Submit goal
POST /goals  (dry_run=true)        # Ghost run (preview only)
GET  /goals/{id}                   # Goal status + result
GET  /goals/{id}/stream            # Live SSE event stream
POST /goals/{id}/pause             # Pause execution
POST /goals/{id}/resume            # Resume execution
POST /goals/{id}/cancel            # Cancel

# === GOVERNANCE ===
GET  /governance/approvals         # Pending HITL approvals
POST /governance/approvals/{id}/approve  # Approve a step
POST /governance/approvals/{id}/reject   # Reject a step
GET  /governance/audit             # Audit log
POST /governance/policies          # Add policy rule
POST /governance/emergency-stop    # KILL SWITCH — halt everything

# === CIVILIZATION ===
POST /civilization                 # Create civilization
GET  /civilization/{id}            # Status + active agents
POST /civilization/{id}/control    # pause/resume/throttle
GET  /civilization/{id}/blackboard # Shared knowledge state
GET  /civilization/{id}/learnings  # What it has learned

# === SCHEDULES ===
POST /schedules                    # Create cron/interval schedule
POST /nl/schedule                  # Create from natural language
GET  /schedules                    # List active schedules
DELETE /schedules/{id}             # Delete schedule
```

---

## Progression Summary

```
Week 1: Simple JIRA Agent
  → Learn goal submission, HITL, basic tool calls
  → Get comfortable with supervised mode
  
Week 2: Multi-Connector Agent  
  → JIRA + GitHub + Slack working together
  → Set up governance policies
  → First scheduled automation

Week 3: Multi-Agent Patterns
  → Supervisor + specialist hierarchy
  → Debate mode for important decisions
  → Visual Workflow Builder

Week 4: Agent Civilization
  → Civilization created and self-managing
  → Constitution rules protecting your data
  → Self-improvement running experiments
  → Agents spawning/retiring autonomously
  → You're now overseeing a team of AI engineers
```

---

*Save this guide at: `docs/agent-creation-guide.md`*
*Platform docs: `docs/features/` — 57 feature-specific deep-dives*
*Operations guide: `RUNBOOK.md`*
