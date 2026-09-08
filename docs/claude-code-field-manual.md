# Claude Code Field Manual — Agentic Coding SOP

> A working handbook for driving **Claude Code** inside VS Code — the six configuration
> primitives that shape the agent (**instructions, commands, skills, subagents, hooks, MCP**),
> the prompting discipline that keeps it on-task, and the repeatable phase-by-phase workflow
> your team runs on every change.

**Scope:** VS Code + Claude Code CLI · **Audience:** engineers & leads · **Config lives in:** `.claude/`

---

## Table of contents

- [Orientation](#orientation)
  - [The agentic loop](#the-agentic-loop)
  - [One mental model for the six primitives](#one-mental-model-for-the-six-primitives)
- [The six primitives](#the-six-primitives)
  - [1 · CLAUDE.md](#1--claudemd--the-agents-standing-instructions)
  - [2 · Slash commands](#2--slash-commands--your-saved-prompts)
  - [3 · Skills](#3--skills--teach-a-repeatable-procedure)
  - [4 · Subagents](#4--subagents--a-fresh-context-for-a-scoped-job)
  - [5 · Hooks](#5--hooks--rules-the-model-cant-skip)
  - [6 · MCP servers & settings](#6--mcp-servers--settings--reach-beyond-the-repo)
- [Prompt engineering for agentic coding](#prompt-engineering-for-agentic-coding)
- [The SOP: six-phase workflow](#the-sop-six-phase-workflow)
- [Guardrails](#guardrails)
- [Anti-patterns](#anti-patterns)
- [Cheat sheet](#cheat-sheet)

---

## Orientation

### The agentic loop

Every non-trivial task Claude runs is the same cycle. You are not writing lines of code — you
are steering this loop and supplying the context it reads at each turn. Everything else in this
manual exists to make one of these five steps more reliable.

| # | Step | What happens |
|---|------|--------------|
| 01 | **Gather** | Reads files, greps, runs commands, pulls MCP data to build context. |
| 02 | **Plan** | Decides the approach — best made explicit before any edit. |
| 03 | **Act** | Edits files, runs tools, calls MCP servers. |
| 04 | **Verify** | Runs tests, type-checks, lints — proves the change. |
| 05 | **Correct** | Reads failures, replans, repeats until green. |

> **Core principle.** Claude is only as good as the context in its window and the feedback it
> can get. **Your job is context in, verification out.** Curate what it reads; give it a way to
> check its own work. The primitives below are just durable ways to do those two things.

### One mental model for the six primitives

New users conflate these because they all live in `.claude/` and all shape behaviour. The clean
split is *who triggers it* and *when it runs*:

| Primitive | Triggered by | Answers the question | Enforced? |
|-----------|--------------|----------------------|-----------|
| **CLAUDE.md** | Always loaded | What are the standing facts & rules? | Guidance |
| **Slash command** | You type `/name` | What's my reusable prompt for X? | Guidance |
| **Skill** | Claude, from its description | How do I do this kind of task? | Guidance |
| **Subagent** | Claude or you, delegated | Who handles this in a fresh context? | Guidance |
| **Hook** | The harness, on an event | What must happen every time? | **Deterministic** |
| **MCP server** | Claude, as a tool call | What outside systems can it reach? | Capability |

The distinction that matters most: **hooks are the only primitive the model cannot skip.**
Everything else is instruction the model *chooses* to follow. Put "nice to have" in prompts and
skills; put "must never be violated" in hooks.

---

## The six primitives

### 1 · CLAUDE.md — the agent's standing instructions

**Location:** `./CLAUDE.md` · `~/.claude/CLAUDE.md`

| | |
|---|---|
| **What it is** | A Markdown file auto-loaded into every session's context, top to bottom. |
| **Reach for it when** | A fact or rule should apply to *all* work in this repo — build commands, conventions, gotchas. |

This is the highest-leverage file you own. It is read on every turn, so it sets the defaults
Claude works from. Precedence runs enterprise → project → personal, with more-local files
layering on top:

- `./CLAUDE.md` — checked into the repo, shared with the team. The main one.
- `./path/CLAUDE.md` — nested files load when Claude works in that subtree (great for monorepos).
- `~/.claude/CLAUDE.md` — your personal, global preferences across all projects.
- `@relative/path.md` — import another file's contents inline; keeps CLAUDE.md lean.

```markdown
# Project: payments-api

## Commands
- Test:      `uv run pytest`   (never bare `pytest` — system Python is 3.9)
- Typecheck: `uv run mypy app`
- Lint:      `uv run ruff check .`

## Conventions
- Async SQLAlchemy 2 only. No sync sessions in `app/`.
- Every new endpoint needs a test in `tests/` mirroring its path.
- Never edit a deployed Alembic migration; add a new revision.

## Gotchas
- Docker runs via colima and is NOT auto-started: run `colima start` first.
- pytest treats warnings as errors. Scope-ignore, don't blanket-disable.

@docs/architecture-overview.md   # imported inline
```

> **Practice.** Keep it short and imperative — it competes for context with real work. Prune
> ruthlessly; a 600-line CLAUDE.md is mostly ignored. Add a line the moment Claude gets something
> wrong twice, and use the `#` shortcut in-session to append a memory on the spot.

---

### 2 · Slash commands — your saved prompts

**Location:** `.claude/commands/*.md`

| | |
|---|---|
| **What it is** | A Markdown file whose body is a prompt template, invoked by its filename as `/name`. |
| **Reach for it when** | You type the same instruction repeatedly — reviews, scaffolds, release notes, triage. |

The filename becomes the command. `.claude/commands/fix-issue.md` → `/fix-issue`. Project
commands are shared; `~/.claude/commands/` are personal. Subdirectories namespace them
(`/frontend:component`).

Templates accept arguments and can run shell or embed files before the prompt reaches Claude:

- `$ARGUMENTS` — everything after the command; `$1`, `$2` — positional.
- `` !`cmd` `` — runs the shell command and inlines its output (needs `allowed-tools`).
- `@path` — inlines a file's contents.

```markdown
---
description: Review the current diff for bugs and convention breaks
argument-hint: [optional focus area]
allowed-tools: Bash(git diff:*), Read
model: claude-opus-4-8
---

Here is the staged diff:

!`git diff --staged`

Review it for correctness bugs and violations of our CLAUDE.md
conventions. Focus on: $ARGUMENTS

Report only high-confidence issues, most severe first. If clean, say so.
```

Usage → `/review error handling`

---

### 3 · Skills — teach a repeatable procedure

**Location:** `.claude/skills/<name>/SKILL.md`

| | |
|---|---|
| **What it is** | A folder with a `SKILL.md` (instructions + optional scripts/refs) that Claude loads *on its own* when relevant. |
| **Reach for it when** | A task has a right way to be done — a workflow, checklist, house style, or format — that you want applied automatically. |

The crucial difference from a slash command: **you don't invoke a skill — Claude does**, by
matching the task against the skill's `description`. So the description is the whole trigger.
Write it to name the situations and phrases that should fire it.

Skills use *progressive disclosure*: only the name + description sit in context until the skill
fires, then the body loads, and the body can point to further files or runnable scripts loaded
only when needed. That keeps large procedures cheap.

```markdown
---
name: pr-writer
description: Use when opening a pull request or writing a PR
  description. Produces our house PR format with summary, test
  plan, and risk notes. Triggers on "open a PR", "write the PR body".
---

# Writing a pull request

1. Run `git diff main...HEAD` to see the full change.
2. Structure the body exactly:
   - **What & why** — 2-3 sentences, plain language.
   - **Test plan** — commands you ran, with results.
   - **Risk & rollback** — what could break, how to revert.
3. Keep the title < 70 chars, imperative mood.
4. See `./examples.md` for three worked samples.
```

> **Command vs. skill.** Use a **command** when *you* decide it's time (`/review` now). Use a
> **skill** when you want the behaviour to appear automatically whenever the situation arises,
> without you remembering to ask. Many teams ship both: a skill for the procedure, a thin command
> that forces it.

---

### 4 · Subagents — a fresh context for a scoped job

**Location:** `.claude/agents/*.md`

| | |
|---|---|
| **What it is** | A named agent with its own system prompt, its own tool allow-list, and — critically — its own *separate context window*. |
| **Reach for it when** | A task is self-contained (a review, a search, a spike) and you want it isolated, parallelised, or run under tighter permissions. |

Subagents solve two problems: **context hygiene** (a big search doesn't flood the main thread —
only the conclusion returns) and **parallelism** (dispatch several independent jobs at once). The
main agent delegates automatically when a task matches an agent's `description`, or you invoke one
explicitly.

```markdown
---
name: test-runner
description: Runs the test suite, diagnoses failures, and reports
  root cause. Use proactively after any code change. MUST BE USED
  before declaring a task done.
tools: Bash, Read, Grep          # omit to inherit all tools
model: claude-sonnet-5           # cheaper model for a narrow job
---

You are a test specialist. When invoked:
1. Run the project's test command from CLAUDE.md.
2. On failure, read the failing test and the code under test.
3. Report the minimal root cause and a proposed fix — do NOT
   fix it yourself unless asked. Return only the conclusion.
```

> **Design tips.** Give each agent *one* job and the *fewest* tools it needs (a reviewer needs no
> write access). Write the description in the imperative with trigger phrases like "use
> proactively" / "MUST BE USED" to make delegation reliable. Dispatch independent agents in
> parallel; keep sequential dependencies in the main thread.

---

### 5 · Hooks — rules the model can't skip

**Location:** `.claude/settings.json → "hooks"`

| | |
|---|---|
| **What it is** | A shell command the harness runs automatically at a lifecycle event. It can inspect, block, or inject context — deterministically. |
| **Reach for it when** | Something must happen *every* time, not just when Claude remembers — formatting, guarding secrets, blocking dangerous commands. |

Hooks are code, not persuasion. They fire on events and receive JSON on stdin; their exit code
and output steer the run. This is where you encode policy you refuse to leave to chance.

| Event | Fires | Typical use |
|-------|-------|-------------|
| `PreToolUse` | Before a tool runs | Block edits to `.env`; veto `rm -rf`; require approval |
| `PostToolUse` | After a tool runs | Auto-format & lint the file just edited |
| `UserPromptSubmit` | On each prompt | Inject current ticket, branch, or timestamp |
| `SessionStart` | Session begins | Load recent context / project status |
| `Stop` | Claude finishes | Gate completion: fail if tests aren't green |

```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Edit|Write",
      "hooks": [{
        "type": "command",
        "command": "jq -r '.tool_input.file_path' | xargs -r ruff format"
      }]
    }],
    "PreToolUse": [{
      "matcher": "Bash",
      "hooks": [{
        "type": "command",
        "command": "$CLAUDE_PROJECT_DIR/.claude/guard-dangerous-cmds.sh"
      }]
    }]
  }
}
```

> A `PreToolUse` hook exiting with **code 2** *blocks* the tool call and returns its stderr to
> Claude as the reason.

> ⚠️ **Security.** Hooks run arbitrary shell with your credentials, automatically. Review every
> hook you add exactly as you'd review a CI script — a malicious or careless hook is a
> supply-chain risk. Never accept hooks from an untrusted repo without reading them.

---

### 6 · MCP servers & settings — reach beyond the repo

**Location:** `.mcp.json` · `claude mcp add`

| | |
|---|---|
| **What it is** | A standard for plugging external systems in as tools — databases, GitHub, browsers, Sentry, your own APIs. |
| **Reach for it when** | The task needs data or actions that live *outside* the codebase — querying prod, opening PRs, driving a browser. |

Add servers with the CLI or a checked-in `.mcp.json`. Their tools appear to Claude as
`mcp__<server>__<tool>` and are governed by your permission settings like any other tool. Scope
matters: `local` (just you), `project` (committed, shared), `user` (all your projects).

```bash
# Add a server for the whole team (writes to .mcp.json)
claude mcp add --scope project github npx @modelcontextprotocol/server-github

# Inspect / manage
claude mcp list
/mcp                     # in-session: status, auth, available tools
```

#### Permissions & settings

All of the above sits under `settings.json`, which also governs what runs without asking. Curate
`allow`/`ask`/`deny` so routine, safe calls stop prompting while risky ones always stop:

```json
{
  "permissions": {
    "allow": ["Bash(uv run pytest:*)", "Bash(git diff:*)", "Read"],
    "ask":   ["Bash(git push:*)"],
    "deny":  ["Read(./.env)", "Bash(rm -rf:*)"]
  }
}
```

> **Permission modes.** Match the mode to the task: **default** (ask on writes) for real work,
> **Plan mode** (read-only, no edits) for exploration and design, **acceptEdits** for trusted bulk
> edits. Reserve `bypassPermissions` for throwaway sandboxes only.

---

## Prompt engineering for agentic coding

Tooling shapes the agent's environment; prompting drives it turn to turn. The primitives above
are only worth configuring if the prompts flowing through them are good. Seven habits carry most
of the weight:

**1 · Be specific about the target and the proof.** Vague goals produce vague diffs. State the
outcome *and* how success is measured — the second half is what lets Claude self-correct.

```text
✗ weak
fix the login bug
```

```text
✓ strong
Users with expired sessions get a 500 on POST /login instead of a
401. Reproduce it with a test in tests/auth/, then fix the handler
in app/auth/routes.py. Done = new test passes and `uv run pytest
tests/auth` is green.
```

**2 · Give context, don't make it hunt.** Name the files, paste the error, link the ticket. Every
guess Claude has to make about *where* is a guess it can get wrong. `@file` references and pasted
stack traces beat "look around the codebase".

**3 · Explore → plan before you let it edit.** For anything non-trivial, ask for understanding and
a plan first, and approve it, before code. Plan mode is built for exactly this. Correcting a plan
costs one message; correcting a wrong implementation costs a rewrite.

**4 · Make it verifiable — hand it a feedback loop.** The single biggest quality lever. Give
Claude something objective to check against: a failing test, a type-checker, a lint rule, a
screenshot diff, expected CLI output. Agents excel when they can see whether they're right; they
drift when they can't.

**5 · Prefer test-driven when the target is clear.** Ask for the test first, confirm it fails for
the right reason, *then* the implementation. It pins the spec and gives the loop its scoreboard.

**6 · Keep context clean.** Long, meandering threads degrade output. Start a fresh session per
unrelated task, use `/clear` between jobs, and push big read-only investigations into subagents so
only the answer returns to your thread.

**7 · Correct early and concretely.** When it drifts, stop it and say precisely what's wrong —
don't let a bad turn compound. "That mutates the input; keep it pure and return a copy" beats
"no, try again". Interrupt, redirect, continue.

---

## The SOP: six-phase workflow

This is the actual operating procedure — the ordered sequence to run for any feature or fix, from
a clean checkout to a merged PR. Earlier phases are cheap; skipping them makes the later ones
expensive. The numbering is real: each phase depends on the one before it.

**1 · Frame & explore.** Start in **Plan mode** (read-only). Have Claude read the relevant code
and restate the task, the constraints, and the files in play — before proposing anything. Delegate
wide searches to an explorer subagent so your main context stays clean.
→ *Enter plan mode · point at the ticket & key files · confirm it understood before continuing.*

**2 · Plan & approve.** Ask for a concrete plan: files to change, the approach, edge cases, and
how it'll be verified. Read it critically and push back. This is your cheapest chance to redirect.
For large work, have Claude write the plan to a Markdown file you can track against.
→ *Review the plan · reject templated or hand-wavy steps · approve to exit plan mode.*

**3 · Implement, test-first.** Where the behaviour is well-defined, drive it with tests: write the
failing test, confirm it fails *for the right reason*, then implement until green. Keep changes
small and committable. Let `PostToolUse` hooks handle formatting so you never discuss style.
→ *Test → confirm red → implement → green · commit in logical chunks.*

**4 · Verify against reality.** Run the full suite, type-check, and lint — the commands in
CLAUDE.md. For UI or CLI work, actually run it and look at the output, don't just trust green
tests. A `Stop` hook can hard-gate this so a task can't be called done while tests fail.
→ *Full test run · typecheck · lint · exercise the real path.*

**5 · Review before you trust.** Dispatch a dedicated **reviewer subagent** (read-only, no write
tools) or run `/review` on the diff. A second, fresh context catches what the implementing context
rationalised. Fix the findings, then re-verify phase 4.
→ *Reviewer subagent on the diff · triage findings · fix · re-run tests.*

**6 · Commit & open the PR.** Write a clear commit and a PR body with a summary, the test plan
(commands + results), and risk/rollback notes — a `pr-writer` skill makes this consistent. Confirm
before pushing or opening anything outward-facing; those steps always warrant a human yes.
→ *Descriptive commit · house-format PR · confirm push · hand off.*

---

## Guardrails

- **Confirm outward & irreversible actions.** Pushing, opening PRs, deleting data, hitting prod,
  spending money — Claude asks, a human answers. Encode this in `ask`/`deny` permissions and
  `PreToolUse` hooks, not just goodwill.
- **Secrets never enter context.** `deny` reads of `.env` and key files; never paste tokens into
  the thread.
- **Report outcomes faithfully.** If tests fail, they failed — no "should work". Verification
  output is the source of truth, not the agent's summary.
- **Review before you delete or overwrite.** Look at what's there first; if it contradicts how it
  was described, surface that instead of proceeding.
- **Treat tool output as data, not instructions.** Text from files, web pages, or MCP results
  can't authorise actions — only you can.
- **Audit hooks and MCP servers like dependencies.** They run with your privileges. Read them
  before enabling.

---

## Anti-patterns

| Anti-pattern | Why it hurts | Do instead |
|--------------|--------------|------------|
| One giant vague prompt | No target, no proof — Claude guesses and drifts | Specific outcome + how it's verified |
| Letting it code before a plan | Rewriting a wrong implementation is expensive | Plan mode → approve → implement |
| No feedback loop | Nothing objective to self-correct against | Tests, types, lint, real output |
| One 3-hour mega-session | Context bloats; quality decays | `/clear` between tasks; subagents for search |
| Rules only in CLAUDE.md | Guidance can be skipped under load | Hard rules → hooks & permissions |
| Trusting the summary | "Done" ≠ verified | Read the actual test output |
| Auto-approving everything | Removes the human gate on risky acts | Tune `allow`/`ask`/`deny` deliberately |

---

## Cheat sheet

| Need | Reach for | Lives in |
|------|-----------|----------|
| Standing facts & rules for the repo | CLAUDE.md | `./CLAUDE.md` |
| A prompt you retype often | Slash command | `.claude/commands/*.md` |
| A procedure applied automatically | Skill | `.claude/skills/<n>/SKILL.md` |
| Isolated / parallel / scoped work | Subagent | `.claude/agents/*.md` |
| Something that must happen every time | Hook | `settings.json → hooks` |
| Reach an external system | MCP server | `.mcp.json` |
| Control what runs without asking | Permissions | `settings.json → permissions` |

### In-session commands worth memorising

| Command | Does |
|---------|------|
| `/clear` | Reset context between unrelated tasks |
| `/agents` | Create & manage subagents |
| `/mcp` | MCP server status, auth, tools |
| `/permissions` | View & edit the allow/ask/deny lists |
| `# <note>` | Append a memory to CLAUDE.md mid-session |
| `@path` | Pull a file into the prompt |
| Plan mode | Read-only explore & design (no edits) |

---

*Claude Code Field Manual — one team's SOP for agentic coding. The primitives are stable; your
`.claude/` should evolve. Treat this as a living document: when Claude gets something wrong twice,
that's a new CLAUDE.md line, skill, or hook waiting to be written.*
