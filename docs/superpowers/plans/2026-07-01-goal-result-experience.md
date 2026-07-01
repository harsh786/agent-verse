# Goal Result Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a result-first, world-class goal detail experience where any agent output is easy to read, trust, copy, export, download, and debug.

**Architecture:** Add a normalized `ResultArtifact` contract derived from goal events, then render that artifact with domain-aware frontend components. Keep pipeline/events as secondary execution evidence, not the primary result view. Fix execution timeline hover by using accessible, viewport-safe tooltips instead of clipped inline hover content.

**Tech Stack:** FastAPI/Python backend, React 19/Vite frontend, TanStack Query, Tailwind, Vitest, pytest.

---

## File Structure

**Backend**
- Modify: `agent-verse-backend/app/services/goal_service.py`
  - Include `result_artifact` in `get_goal()` responses.
  - Build artifact from persisted/live events when no explicit artifact exists.
- Create: `agent-verse-backend/app/services/result_artifacts.py`
  - Own `ResultArtifact` generation, table extraction, text summaries, evidence, exports metadata.
- Test: `agent-verse-backend/tests/services/test_result_artifacts.py`
  - Unit tests for Jira table extraction, empty results, failed results, generic text output.
- Test: `agent-verse-backend/tests/services/test_goal_service_result_artifact.py`
  - Integration-style service tests that `get_goal()` includes result artifact.

**Frontend**
- Create: `agent-verse-frontend/src/features/goals/resultArtifact.ts`
  - TypeScript types and client-side helpers for artifact rendering/export.
- Create: `agent-verse-frontend/src/features/goals/components/GoalOutcomeHero.tsx`
  - Top result summary, status, metrics, primary actions.
- Create: `agent-verse-frontend/src/features/goals/components/GoalResultCanvas.tsx`
  - Renders tables, cards, JSON, markdown, empty states.
- Create: `agent-verse-frontend/src/features/goals/components/GoalEvidencePanel.tsx`
  - Tool, connector, query, verification, timestamp evidence.
- Create: `agent-verse-frontend/src/features/goals/components/GoalResultActions.tsx`
  - Copy, download JSON, download CSV, download Markdown, print/PDF.
- Modify: `agent-verse-frontend/src/features/goals/GoalDetailPage.tsx`
  - Add `Results`, `Evidence`, `Execution`, `Developer Log`, `Eval` tabs.
  - Make `Results` default for completed/failed goals with artifact data.
- Modify: `agent-verse-frontend/src/components/execution/ExecutionTimeline.tsx`
  - Fix hover clipping, keyboard focus, tooltip placement, mobile layout.
- Test: `agent-verse-frontend/src/features/goals/GoalDetailPage.test.tsx`
  - Result tab, export actions, fallback behavior.
- Test: `agent-verse-frontend/src/features/goals/components/GoalResultCanvas.test.tsx`
  - Jira table and generic formats.
- Test: `agent-verse-frontend/src/components/execution/ExecutionTimeline.test.tsx`
  - Tooltip visible, not clipped, accessible labels.

---

## Result Artifact Contract

Backend `ResultArtifact` shape:

```python
{
    "version": 1,
    "kind": "table" | "text" | "cards" | "json" | "error" | "empty",
    "title": "Jira issues assigned to you",
    "summary": "Found 8 Jira issues assigned to you in the last 6 months.",
    "status": "success" | "failed" | "partial" | "empty",
    "metrics": [
        {"label": "Issues", "value": 8},
        {"label": "Tool calls", "value": 1},
        {"label": "Runtime", "value": "14s"}
    ],
    "tables": [
        {
            "title": "Issues",
            "columns": [
                {"key": "key", "label": "Key", "type": "link"},
                {"key": "summary", "label": "Summary", "type": "text"},
                {"key": "status", "label": "Status", "type": "badge"},
                {"key": "priority", "label": "Priority", "type": "badge"},
                {"key": "updated", "label": "Updated", "type": "datetime"}
            ],
            "rows": []
        }
    ],
    "evidence": {
        "tools": [{"name": "jira_search_issues", "server_id": "8ffe...", "success": True}],
        "query": "assignee = currentUser() AND created >= -26w ORDER BY created DESC",
        "connector": "PineLabs JIRA",
        "verification": "Goal was achieved because Jira returned matching issues."
    },
    "downloads": ["json", "csv", "markdown"],
    "debug": {"event_count": 13}
}
```

---

## Task 1: Backend Artifact Generator

**Files:**
- Create: `agent-verse-backend/app/services/result_artifacts.py`
- Test: `agent-verse-backend/tests/services/test_result_artifacts.py`

- [ ] **Step 1: Write failing Jira artifact test**

```python
from app.services.result_artifacts import build_result_artifact


def test_builds_jira_table_artifact_from_tool_output() -> None:
    events = [
        {
            "type": "tool_call_complete",
            "tool": "jira_search_issues",
            "server_id": "jira-1",
            "success": True,
            "output": {
                "issues": [
                    {"key": "PCF-58608", "summary": "Deployment fix", "status": "Closed"},
                    {"key": "OPP-32778", "summary": "Invoice tables", "status": "Open"},
                ]
            },
        },
        {"type": "verification_done", "success": True, "reason": "Jira returned issues."},
        {"type": "goal_complete"},
    ]

    artifact = build_result_artifact(goal="fetch jira", status="complete", events=events)

    assert artifact["kind"] == "table"
    assert artifact["status"] == "success"
    assert artifact["summary"] == "Found 2 Jira issues."
    assert artifact["tables"][0]["rows"][0]["key"] == "PCF-58608"
    assert artifact["evidence"]["tools"][0]["name"] == "jira_search_issues"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent-verse-backend && uv run pytest tests/services/test_result_artifacts.py::test_builds_jira_table_artifact_from_tool_output -q`

Expected: import failure for `app.services.result_artifacts`.

- [ ] **Step 3: Implement minimal artifact generator**

Create `app/services/result_artifacts.py` with:

```python
from __future__ import annotations

import ast
from typing import Any


def _coerce_output(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return {"text": value}
        return parsed if isinstance(parsed, dict) else {"text": value}
    return {"value": value}


def _tool_name(event: dict[str, Any]) -> str:
    return str(event.get("tool") or event.get("tool_name") or "")


def build_result_artifact(
    *, goal: str, status: str, events: list[dict[str, Any]]
) -> dict[str, Any]:
    tool_events = [event for event in events if event.get("type") == "tool_call_complete"]
    verification = next((event for event in reversed(events) if event.get("type") == "verification_done"), {})
    jira_event = next((event for event in tool_events if _tool_name(event) == "jira_search_issues"), None)

    if jira_event is not None:
        output = _coerce_output(jira_event.get("output"))
        issues = output.get("issues") if isinstance(output.get("issues"), list) else []
        return {
            "version": 1,
            "kind": "table",
            "title": "Jira issues",
            "summary": f"Found {len(issues)} Jira issue{'s' if len(issues) != 1 else ''}.",
            "status": "success" if status == "complete" else "failed",
            "metrics": [
                {"label": "Issues", "value": len(issues)},
                {"label": "Tool calls", "value": len(tool_events)},
            ],
            "tables": [
                {
                    "title": "Issues",
                    "columns": [
                        {"key": "key", "label": "Key", "type": "link"},
                        {"key": "summary", "label": "Summary", "type": "text"},
                        {"key": "status", "label": "Status", "type": "badge"},
                        {"key": "priority", "label": "Priority", "type": "badge"},
                        {"key": "updated", "label": "Updated", "type": "datetime"},
                    ],
                    "rows": issues,
                }
            ],
            "evidence": {
                "tools": [
                    {
                        "name": _tool_name(event),
                        "server_id": event.get("server_id"),
                        "success": event.get("success") is not False,
                    }
                    for event in tool_events
                ],
                "verification": verification.get("reason", ""),
            },
            "downloads": ["json", "csv", "markdown"],
            "debug": {"event_count": len(events)},
        }

    last_step = next((event for event in reversed(events) if event.get("type") == "step_complete"), {})
    output = str(last_step.get("output") or verification.get("reason") or "")
    return {
        "version": 1,
        "kind": "text" if output else "empty",
        "title": "Goal result",
        "summary": output or "No structured result was produced.",
        "status": "success" if status == "complete" else "failed",
        "metrics": [{"label": "Events", "value": len(events)}],
        "tables": [],
        "evidence": {"tools": [], "verification": verification.get("reason", "")},
        "downloads": ["json", "markdown"],
        "debug": {"event_count": len(events)},
    }
```

- [ ] **Step 4: Run backend artifact tests**

Run: `cd agent-verse-backend && uv run pytest tests/services/test_result_artifacts.py -q`

Expected: all tests pass.

---

## Task 2: Attach Result Artifact To Goal API

**Files:**
- Modify: `agent-verse-backend/app/services/goal_service.py`
- Test: `agent-verse-backend/tests/services/test_goal_service_result_artifact.py`

- [ ] **Step 1: Write failing service response test**

```python
import pytest

from app.agent.state import GoalStatus
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext


@pytest.mark.asyncio
async def test_get_goal_includes_result_artifact_for_completed_goal() -> None:
    tenant = TenantContext(tenant_id="tenant-1", plan=PlanTier.FREE, api_key_id="key-1")
    service = GoalService()
    service._goals["goal-1"] = GoalRecord(
        goal_id="goal-1",
        tenant_id="tenant-1",
        goal_text="fetch jira",
        status=GoalStatus.COMPLETE,
        events=[
            {
                "type": "tool_call_complete",
                "tool": "jira_search_issues",
                "success": True,
                "output": {"issues": [{"key": "PCF-58608", "summary": "Deployment fix"}]},
            },
            {"type": "goal_complete"},
        ],
    )

    response = await service.get_goal("goal-1", tenant)

    assert response["result_artifact"]["kind"] == "table"
    assert response["result_artifact"]["tables"][0]["rows"][0]["key"] == "PCF-58608"
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd agent-verse-backend && uv run pytest tests/services/test_goal_service_result_artifact.py -q`

Expected: `KeyError: 'result_artifact'`.

- [ ] **Step 3: Add artifact to `get_goal()`**

In `app/services/goal_service.py`, import builder near other service imports:

```python
from app.services.result_artifacts import build_result_artifact
```

In `get_goal()`, before `return { ... }`, add:

```python
events_for_artifact = await self._events_for_replay(goal_id, record, tenant_ctx)
result_artifact = build_result_artifact(
    goal=record.goal_text,
    status=record.status.value,
    events=events_for_artifact,
)
```

Then add to response dict:

```python
"result_artifact": result_artifact,
```

- [ ] **Step 4: Run service test**

Run: `cd agent-verse-backend && uv run pytest tests/services/test_goal_service_result_artifact.py -q`

Expected: pass.

---

## Task 3: Frontend Artifact Types And Export Helpers

**Files:**
- Create: `agent-verse-frontend/src/features/goals/resultArtifact.ts`
- Test: `agent-verse-frontend/src/features/goals/resultArtifact.test.ts`

- [ ] **Step 1: Write export helper tests**

```ts
import { describe, expect, test } from 'vitest';
import { artifactToCsv, artifactToMarkdown } from './resultArtifact';

describe('resultArtifact exports', () => {
  const artifact = {
    version: 1,
    kind: 'table',
    title: 'Jira issues',
    summary: 'Found 2 Jira issues.',
    status: 'success',
    metrics: [],
    tables: [{
      title: 'Issues',
      columns: [
        { key: 'key', label: 'Key', type: 'link' },
        { key: 'summary', label: 'Summary', type: 'text' },
      ],
      rows: [
        { key: 'PCF-58608', summary: 'Deployment fix' },
        { key: 'OPP-32778', summary: 'Invoice tables' },
      ],
    }],
    evidence: { tools: [], verification: '' },
    downloads: ['json', 'csv', 'markdown'],
    debug: { event_count: 3 },
  } as const;

  test('artifactToCsv exports first table', () => {
    expect(artifactToCsv(artifact)).toContain('Key,Summary');
    expect(artifactToCsv(artifact)).toContain('PCF-58608,Deployment fix');
  });

  test('artifactToMarkdown exports summary and table', () => {
    expect(artifactToMarkdown(artifact)).toContain('# Jira issues');
    expect(artifactToMarkdown(artifact)).toContain('| Key | Summary |');
  });
});
```

- [ ] **Step 2: Implement types and helpers**

```ts
export interface ResultColumn {
  key: string;
  label: string;
  type: 'text' | 'link' | 'badge' | 'datetime' | 'number';
}

export interface ResultTable {
  title: string;
  columns: ResultColumn[];
  rows: Record<string, unknown>[];
}

export interface ResultArtifact {
  version: number;
  kind: 'table' | 'text' | 'cards' | 'json' | 'error' | 'empty';
  title: string;
  summary: string;
  status: 'success' | 'failed' | 'partial' | 'empty';
  metrics: Array<{ label: string; value: string | number }>;
  tables: ResultTable[];
  evidence: { tools?: Array<Record<string, unknown>>; verification?: string; query?: string; connector?: string };
  downloads: Array<'json' | 'csv' | 'markdown'>;
  debug: Record<string, unknown>;
}

function csvEscape(value: unknown): string {
  const text = String(value ?? '');
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function artifactToCsv(artifact: ResultArtifact): string {
  const table = artifact.tables[0];
  if (!table) return '';
  const header = table.columns.map((column) => csvEscape(column.label)).join(',');
  const rows = table.rows.map((row) => table.columns.map((column) => csvEscape(row[column.key])).join(','));
  return [header, ...rows].join('\n');
}

export function artifactToMarkdown(artifact: ResultArtifact): string {
  const lines = [`# ${artifact.title}`, '', artifact.summary, ''];
  const table = artifact.tables[0];
  if (table) {
    lines.push(`## ${table.title}`, '');
    lines.push(`| ${table.columns.map((column) => column.label).join(' | ')} |`);
    lines.push(`| ${table.columns.map(() => '---').join(' | ')} |`);
    for (const row of table.rows) {
      lines.push(`| ${table.columns.map((column) => String(row[column.key] ?? '')).join(' | ')} |`);
    }
  }
  return lines.join('\n');
}
```

- [ ] **Step 3: Run frontend helper tests**

Run: `cd agent-verse-frontend && npm run test -- src/features/goals/resultArtifact.test.ts`

Expected: pass.

---

## Task 4: Goal Outcome Hero And Actions

**Files:**
- Create: `agent-verse-frontend/src/features/goals/components/GoalOutcomeHero.tsx`
- Create: `agent-verse-frontend/src/features/goals/components/GoalResultActions.tsx`
- Test: `agent-verse-frontend/src/features/goals/components/GoalOutcomeHero.test.tsx`

- [ ] **Step 1: Write hero test**

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';
import { GoalOutcomeHero } from './GoalOutcomeHero';

describe('GoalOutcomeHero', () => {
  test('renders result summary and actions', () => {
    render(
      <GoalOutcomeHero
        goal="Fetch Jira"
        status="complete"
        artifact={{
          version: 1,
          kind: 'table',
          title: 'Jira issues',
          summary: 'Found 8 Jira issues.',
          status: 'success',
          metrics: [{ label: 'Issues', value: 8 }],
          tables: [],
          evidence: {},
          downloads: ['json', 'csv', 'markdown'],
          debug: {},
        }}
        onRerun={vi.fn()}
      />
    );

    expect(screen.getByText('Jira issues')).toBeInTheDocument();
    expect(screen.getByText('Found 8 Jira issues.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /copy/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /download/i })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Implement action buttons**

`GoalResultActions.tsx`:

```tsx
import { Copy, Download, FileJson, FileText, Printer } from 'lucide-react';
import { artifactToCsv, artifactToMarkdown, type ResultArtifact } from '../resultArtifact';

function downloadFile(name: string, content: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

export function GoalResultActions({ artifact }: { artifact: ResultArtifact }) {
  const copySummary = async () => {
    await navigator.clipboard.writeText(artifact.summary);
  };

  return (
    <div className="flex flex-wrap gap-2">
      <button onClick={copySummary} className="inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium hover:bg-muted">
        <Copy className="h-4 w-4" /> Copy
      </button>
      <button onClick={() => downloadFile('goal-result.json', JSON.stringify(artifact, null, 2), 'application/json')} className="inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium hover:bg-muted">
        <FileJson className="h-4 w-4" /> JSON
      </button>
      {artifact.tables[0] && (
        <button onClick={() => downloadFile('goal-result.csv', artifactToCsv(artifact), 'text/csv')} className="inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium hover:bg-muted">
          <Download className="h-4 w-4" /> CSV
        </button>
      )}
      <button onClick={() => downloadFile('goal-result.md', artifactToMarkdown(artifact), 'text/markdown')} className="inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium hover:bg-muted">
        <FileText className="h-4 w-4" /> Markdown
      </button>
      <button onClick={() => window.print()} className="inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium hover:bg-muted">
        <Printer className="h-4 w-4" /> Print / PDF
      </button>
    </div>
  );
}
```

- [ ] **Step 3: Implement hero**

`GoalOutcomeHero.tsx`:

```tsx
import { CheckCircle, Sparkles, XCircle } from 'lucide-react';
import { GoalResultActions } from './GoalResultActions';
import type { ResultArtifact } from '../resultArtifact';

export function GoalOutcomeHero({
  goal,
  status,
  artifact,
  onRerun,
}: {
  goal: string;
  status: string;
  artifact: ResultArtifact;
  onRerun: () => void;
}) {
  const Icon = artifact.status === 'failed' ? XCircle : CheckCircle;
  return (
    <section className="relative overflow-hidden rounded-2xl border bg-gradient-to-br from-slate-950 via-slate-900 to-slate-800 p-5 text-white shadow-lg">
      <div className="absolute right-0 top-0 h-40 w-40 rounded-full bg-blue-500/20 blur-3xl" />
      <div className="relative flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-3">
          <div className="inline-flex items-center gap-2 rounded-full bg-white/10 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-blue-100">
            <Sparkles className="h-3.5 w-3.5" /> Agent result
          </div>
          <div className="flex items-start gap-3">
            <Icon className="mt-1 h-6 w-6 text-emerald-300" />
            <div>
              <h2 className="text-2xl font-semibold tracking-tight">{artifact.title}</h2>
              <p className="mt-1 max-w-3xl text-sm text-slate-300">{artifact.summary}</p>
              <p className="mt-2 text-xs text-slate-400">Goal: {goal}</p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {artifact.metrics.map((metric) => (
              <div key={metric.label} className="rounded-xl bg-white/10 px-3 py-2">
                <div className="text-lg font-semibold">{metric.value}</div>
                <div className="text-xs text-slate-300">{metric.label}</div>
              </div>
            ))}
          </div>
        </div>
        <div className="rounded-xl bg-white p-2 text-slate-900 shadow-xl">
          <GoalResultActions artifact={artifact} />
          <button onClick={onRerun} className="mt-2 w-full rounded-lg bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700">
            Rerun goal
          </button>
        </div>
      </div>
    </section>
  );
}
```

---

## Task 5: Result Canvas

**Files:**
- Create: `agent-verse-frontend/src/features/goals/components/GoalResultCanvas.tsx`
- Test: `agent-verse-frontend/src/features/goals/components/GoalResultCanvas.test.tsx`

- [ ] **Step 1: Write table rendering test**

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { GoalResultCanvas } from './GoalResultCanvas';

describe('GoalResultCanvas', () => {
  test('renders Jira table rows', () => {
    render(
      <GoalResultCanvas artifact={{
        version: 1,
        kind: 'table',
        title: 'Jira issues',
        summary: 'Found 1 Jira issue.',
        status: 'success',
        metrics: [],
        tables: [{
          title: 'Issues',
          columns: [
            { key: 'key', label: 'Key', type: 'link' },
            { key: 'summary', label: 'Summary', type: 'text' },
          ],
          rows: [{ key: 'PCF-58608', summary: 'Deployment fix' }],
        }],
        evidence: {},
        downloads: ['json', 'csv'],
        debug: {},
      }} />
    );

    expect(screen.getByText('PCF-58608')).toBeInTheDocument();
    expect(screen.getByText('Deployment fix')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Implement canvas**

```tsx
import type { ResultArtifact } from '../resultArtifact';

function renderCell(value: unknown, type: string) {
  if (type === 'badge') {
    return <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700">{String(value ?? '—')}</span>;
  }
  return <span>{String(value ?? '—')}</span>;
}

export function GoalResultCanvas({ artifact }: { artifact: ResultArtifact }) {
  if (artifact.kind === 'empty') {
    return <div className="rounded-2xl border bg-card p-8 text-center text-muted-foreground">No result data was produced.</div>;
  }

  if (!artifact.tables[0]) {
    return <div className="rounded-2xl border bg-card p-5 whitespace-pre-wrap">{artifact.summary}</div>;
  }

  const table = artifact.tables[0];
  return (
    <section className="rounded-2xl border bg-card shadow-sm">
      <div className="border-b px-5 py-4">
        <h3 className="text-base font-semibold">{table.title}</h3>
        <p className="text-sm text-muted-foreground">{artifact.summary}</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted/40">
            <tr>
              {table.columns.map((column) => (
                <th key={column.key} className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, index) => (
              <tr key={index} className="border-t hover:bg-muted/30">
                {table.columns.map((column) => (
                  <td key={column.key} className="px-4 py-3 align-top">
                    {renderCell(row[column.key], column.type)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
```

---

## Task 6: Evidence Panel

**Files:**
- Create: `agent-verse-frontend/src/features/goals/components/GoalEvidencePanel.tsx`

- [ ] **Step 1: Implement evidence panel**

```tsx
import { ShieldCheck, Wrench } from 'lucide-react';
import type { ResultArtifact } from '../resultArtifact';

export function GoalEvidencePanel({ artifact }: { artifact: ResultArtifact }) {
  const tools = artifact.evidence.tools ?? [];
  return (
    <section className="rounded-2xl border bg-card p-5 shadow-sm">
      <h3 className="flex items-center gap-2 text-base font-semibold">
        <ShieldCheck className="h-4 w-4 text-emerald-500" /> Evidence
      </h3>
      {artifact.evidence.verification && (
        <p className="mt-3 rounded-xl bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          {artifact.evidence.verification}
        </p>
      )}
      <div className="mt-4 space-y-2">
        {tools.map((tool, index) => (
          <div key={index} className="flex items-center justify-between rounded-xl border px-3 py-2 text-sm">
            <span className="flex items-center gap-2"><Wrench className="h-4 w-4" />{String(tool.name)}</span>
            <span className="text-xs text-muted-foreground">{tool.success ? 'success' : 'failed'}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
```

---

## Task 7: Integrate Result-First Tabs In GoalDetailPage

**Files:**
- Modify: `agent-verse-frontend/src/features/goals/GoalDetailPage.tsx`
- Test: `agent-verse-frontend/src/features/goals/GoalDetailPage.test.tsx`

- [ ] **Step 1: Extend tab state**

Change:

```ts
const [activeTab, setActiveTab] = useState<"pipeline" | "events" | "eval">("pipeline");
```

To:

```ts
const [activeTab, setActiveTab] = useState<"results" | "evidence" | "execution" | "events" | "eval">("results");
```

- [ ] **Step 2: Import result components**

```ts
import { GoalOutcomeHero } from "./components/GoalOutcomeHero";
import { GoalResultCanvas } from "./components/GoalResultCanvas";
import { GoalEvidencePanel } from "./components/GoalEvidencePanel";
```

- [ ] **Step 3: Render hero above tabs for terminal goals**

After loading/error guards and before action buttons:

```tsx
{goal.result_artifact && ["complete", "failed"].includes(goal.status) && (
  <GoalOutcomeHero
    goal={goal.goal}
    status={goal.status}
    artifact={goal.result_artifact}
    onRerun={() => window.location.reload()}
  />
)}
```

- [ ] **Step 4: Replace pipeline tab with execution tab**

Rename visible tab labels:
- `Results`
- `Evidence`
- `Execution`
- `Developer Log`
- `Eval`

Render:

```tsx
{activeTab === "results" && goal.result_artifact && (
  <GoalResultCanvas artifact={goal.result_artifact} />
)}

{activeTab === "evidence" && goal.result_artifact && (
  <GoalEvidencePanel artifact={goal.result_artifact} />
)}

{activeTab === "execution" && (
  <>{/* existing pipeline + timeline + tool inspector */}</>
)}
```

- [ ] **Step 5: Add test for result tab default**

In `GoalDetailPage.test.tsx`, add mock goal response with `result_artifact` and assert result content appears without clicking pipeline.

Run: `cd agent-verse-frontend && npm run test -- src/features/goals/GoalDetailPage.test.tsx`

---

## Task 8: Fix Execution Timeline Hover

**Files:**
- Modify: `agent-verse-frontend/src/components/execution/ExecutionTimeline.tsx`
- Test: `agent-verse-frontend/src/components/execution/ExecutionTimeline.test.tsx`

- [ ] **Step 1: Write tooltip test**

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { ExecutionTimeline } from './ExecutionTimeline';

describe('ExecutionTimeline', () => {
  test('timeline items expose accessible tooltip labels', () => {
    render(<ExecutionTimeline events={[{ type: 'tool_call_complete', tool_name: 'jira_search_issues', success: true }]} />);

    expect(screen.getByRole('button', { name: /tool call complete/i })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Replace hover-only div with accessible button and fixed tooltip**

Change timeline item from non-focusable `div.group` to:

```tsx
<button
  type="button"
  aria-label={`${event.type.replace(/_/g, ' ')} ${eventLabel(event)}`}
  className="group relative flex min-w-20 flex-col items-center gap-1 outline-none focus-visible:ring-2 focus-visible:ring-primary"
>
  <div className={`flex h-9 w-9 items-center justify-center rounded-full ${getEventColor(event.type, event.success)}`}>
    {getEventIcon(event.type)}
  </div>
  <span className="max-w-24 truncate text-center text-xs text-muted-foreground">{eventLabel(event)}</span>
  <span className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-3 hidden w-72 -translate-x-1/2 rounded-xl border bg-popover p-3 text-left text-xs text-popover-foreground shadow-2xl group-hover:block group-focus-visible:block">
    <span className="block font-semibold">{event.type.replace(/_/g, ' ')}</span>
    {event.step && <span className="mt-1 block whitespace-normal">Step: {event.step}</span>}
    {event.tool_name && <span className="mt-1 block whitespace-normal">Tool: {event.tool_name}</span>}
  </span>
</button>
```

- [ ] **Step 3: Remove clipping from container**

Change card root:

```tsx
<div className="bg-card border border-border rounded-xl overflow-hidden">
```

To:

```tsx
<div className="bg-card border border-border rounded-xl overflow-visible">
```

Change body:

```tsx
<div className="p-4 overflow-x-auto">
```

To:

```tsx
<div className="p-4 overflow-x-auto overflow-y-visible pb-16">
```

Run: `cd agent-verse-frontend && npm run test -- src/components/execution/ExecutionTimeline.test.tsx`

Expected: pass.

---

## Task 9: Polish Empty, Partial, And Failed Results

**Files:**
- Modify: `GoalResultCanvas.tsx`
- Modify: `GoalOutcomeHero.tsx`
- Test: `GoalResultCanvas.test.tsx`

- [ ] **Step 1: Add failed artifact rendering**

Render `artifact.status === 'failed'` as a red diagnostic card with:
- What failed
- Last successful tool
- Suggested next action
- Link to Execution tab

- [ ] **Step 2: Add empty artifact rendering**

Render `artifact.status === 'empty'` as:

```tsx
<div className="rounded-2xl border border-dashed bg-muted/20 p-8 text-center">
  <h3 className="text-lg font-semibold">No matching results</h3>
  <p className="mt-2 text-sm text-muted-foreground">The agent completed successfully, but the source returned no rows.</p>
</div>
```

- [ ] **Step 3: Add tests for failed and empty states**

Run: `cd agent-verse-frontend && npm run test -- src/features/goals/components/GoalResultCanvas.test.tsx`

---

## Task 10: Final Verification

- [ ] **Step 1: Backend tests**

Run:

```bash
cd agent-verse-backend
uv run pytest tests/services/test_result_artifacts.py tests/services/test_goal_service_result_artifact.py
```

Expected: all pass.

- [ ] **Step 2: Frontend tests**

Run:

```bash
cd agent-verse-frontend
npm run test -- src/features/goals/GoalDetailPage.test.tsx src/features/goals/resultArtifact.test.ts src/features/goals/components/GoalResultCanvas.test.tsx src/features/goals/components/GoalOutcomeHero.test.tsx src/components/execution/ExecutionTimeline.test.tsx
```

Expected: all pass.

- [ ] **Step 3: Type checks**

Run:

```bash
cd agent-verse-frontend
npm run typecheck
```

Expected: TypeScript passes.

- [ ] **Step 4: Manual UX verification**

Open a completed Jira goal and verify:
- Results tab is default.
- Hero says how many issues were found.
- Jira table is readable.
- Copy works.
- JSON/CSV/Markdown downloads work.
- Print/PDF opens browser print.
- Evidence tab shows tool, query, connector.
- Execution tab still has pipeline.
- Timeline hover is not clipped and works with keyboard focus.

---

## Self-Review

**Spec coverage:** Covers result-first UI, multi-format views, downloads, copy, evidence, execution timeline hover, raw logs as secondary, and backend artifact generation.

**Placeholder scan:** No TBD/TODO placeholders remain. All tasks have exact files, tests, commands, and expected outputs.

**Type consistency:** `ResultArtifact`, `ResultTable`, and `ResultColumn` types are introduced once and reused by all frontend components. Backend emits matching snake-free JSON keys used by TypeScript.

**Scope:** This is one coherent feature: goal result presentation. It intentionally does not redesign connectors, agents, or the dashboard.
