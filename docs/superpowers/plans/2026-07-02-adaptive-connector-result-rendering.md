# Adaptive Connector Result Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render connector/tool outputs as readable tables, text, diagnostics, or JSON fallback based on response shape rather than connector name.

**Architecture:** Add a frontend-only adaptive result normalizer that converts unknown tool output into a stable view model, then render that model through a reusable `AdaptiveResultPanel`. Integrate the panel into execution step expansion and the tool call inspector while keeping the existing `GoalResultCanvas` for backend result artifacts.

**Tech Stack:** React 19, TypeScript, Vite, Vitest, React Testing Library, Tailwind utility classes.

---

## File Structure

- Create: `agent-verse-frontend/src/features/goals/adaptiveResult.ts`
  - Owns connector-agnostic output normalization, field detection, metric extraction, table inference, text fallback, diagnostic fallback, and JSON fallback decisions.
- Create: `agent-verse-frontend/src/features/goals/adaptiveResult.test.ts`
  - Unit tests for Jira-style, GitHub-style, Linear-style, Slack-style, text, error, empty, and unknown JSON outputs.
- Create: `agent-verse-frontend/src/features/goals/components/AdaptiveResultPanel.tsx`
  - Renders `AdaptiveResultViewModel` as summary metrics, diagnostics, table, text, or collapsible raw JSON.
- Create: `agent-verse-frontend/src/features/goals/components/AdaptiveResultPanel.test.tsx`
  - Component tests for accessible metrics, table rendering, diagnostic rendering, raw JSON disclosure, and text output.
- Modify: `agent-verse-frontend/src/features/goals/GoalDetailPage.tsx`
  - Replace raw expanded `tool_call_complete` output text with `AdaptiveResultPanel` when an output/error exists.
- Modify: `agent-verse-frontend/src/features/goals/GoalDetailPage.test.tsx`
  - Verify expanded pipeline rows show readable connector output instead of only raw JSON text.
- Modify: `agent-verse-frontend/src/components/execution/ToolCallInspector.tsx`
  - Replace raw output `<pre>` with `AdaptiveResultPanel`; keep arguments as raw JSON because they are request/debug data.
- Create or modify: `agent-verse-frontend/src/components/execution/ToolCallInspector.test.tsx`
  - If the file exists, add tests there. If it does not exist, create it with focused tests for adaptive output rendering.

---

### Task 1: Add Adaptive Result Normalizer

**Files:**
- Create: `agent-verse-frontend/src/features/goals/adaptiveResult.ts`
- Create: `agent-verse-frontend/src/features/goals/adaptiveResult.test.ts`

- [ ] **Step 1: Write failing normalizer tests**

Create `agent-verse-frontend/src/features/goals/adaptiveResult.test.ts` with these tests:

```ts
import { describe, expect, test } from 'vitest';
import { normalizeAdaptiveResult } from './adaptiveResult';

describe('normalizeAdaptiveResult', () => {
  test('renders Jira-style issues as a table with metrics', () => {
    const result = normalizeAdaptiveResult(
      {
        total: 10,
        max_results: 50,
        issues: [
          {
            key: 'OPP-34746',
            summary: 'Removed Logging in files in txn data service',
            status: 'To be deployed',
            assignee: 'Abhay Dwivedi',
            updated: '2026-06-29T12:56:40.089+0530',
          },
        ],
      },
      { toolName: 'jira_search_issues', serverId: 'jira', success: true }
    );

    expect(result.status).toBe('success');
    expect(result.primaryView).toBe('table');
    expect(result.metrics).toContainEqual({ label: 'Total', value: 10 });
    expect(result.metrics).toContainEqual({ label: 'Returned', value: 1 });
    expect(result.table?.columns.map((column) => column.key)).toEqual([
      'key',
      'summary',
      'status',
      'assignee',
      'updated',
    ]);
    expect(result.table?.rows[0].key).toBe('OPP-34746');
  });

  test('renders GitHub-style results arrays as a table', () => {
    const result = normalizeAdaptiveResult(
      {
        total_count: 2,
        items: [
          {
            number: 1842,
            title: 'Fix auth middleware',
            state: 'open',
            user: { login: 'octocat' },
            html_url: 'https://github.com/acme/repo/pull/1842',
          },
        ],
      },
      { toolName: 'github_search_issues', serverId: 'github', success: true }
    );

    expect(result.primaryView).toBe('table');
    expect(result.metrics).toContainEqual({ label: 'Total', value: 2 });
    expect(result.table?.rows[0].owner).toBe('octocat');
    expect(result.table?.rows[0].url).toBe('https://github.com/acme/repo/pull/1842');
  });

  test('unwraps Linear-style nodes arrays', () => {
    const result = normalizeAdaptiveResult(
      {
        data: {
          issues: {
            nodes: [
              {
                identifier: 'LIN-1',
                title: 'Renew certificate',
                state: { name: 'Todo' },
                assignee: { name: 'SRE' },
              },
            ],
          },
        },
      },
      { toolName: 'linear_list_issues', success: true }
    );

    expect(result.primaryView).toBe('table');
    expect(result.table?.rows[0].identifier).toBe('LIN-1');
    expect(result.table?.rows[0].status).toBe('Todo');
    expect(result.table?.rows[0].assignee).toBe('SRE');
  });

  test('renders Slack-style messages arrays as a table', () => {
    const result = normalizeAdaptiveResult(
      {
        messages: [
          {
            user: 'U123',
            text: 'Deployment completed',
            ts: '2026-07-02T10:00:00Z',
          },
        ],
      },
      { toolName: 'slack_search_messages', success: true }
    );

    expect(result.primaryView).toBe('table');
    expect(result.table?.columns.map((column) => column.key)).toContain('text');
    expect(result.table?.rows[0].text).toBe('Deployment completed');
  });

  test('renders plain strings as text', () => {
    const result = normalizeAdaptiveResult('All checks passed.', {
      toolName: 'shell_execute',
      success: true,
    });

    expect(result.primaryView).toBe('text');
    expect(result.text).toBe('All checks passed.');
  });

  test('renders failed tool output as diagnostics', () => {
    const result = normalizeAdaptiveResult(
      { error: 'HTTP 401: Unauthorized' },
      { toolName: 'jira_search_issues', serverId: 'jira', success: false }
    );

    expect(result.status).toBe('failed');
    expect(result.primaryView).toBe('diagnostic');
    expect(result.diagnostics).toContainEqual({ label: 'Tool', value: 'jira_search_issues' });
    expect(result.diagnostics).toContainEqual({ label: 'Error', value: 'HTTP 401: Unauthorized' });
  });

  test('renders empty successful arrays as an empty diagnostic result', () => {
    const result = normalizeAdaptiveResult(
      { total: 0, issues: [], jql: 'assignee = "Nobody"' },
      { toolName: 'jira_search_issues', success: true }
    );

    expect(result.status).toBe('empty');
    expect(result.primaryView).toBe('diagnostic');
    expect(result.diagnostics).toContainEqual({ label: 'Query', value: 'assignee = "Nobody"' });
    expect(result.diagnostics.some((item) => item.value.includes('No rows'))).toBe(true);
  });

  test('falls back to JSON for unknown nested objects', () => {
    const output = { nested: { value: { deep: true } } };
    const result = normalizeAdaptiveResult(output, { toolName: 'unknown_tool', success: true });

    expect(result.primaryView).toBe('json');
    expect(result.raw).toBe(output);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
npm run test -- src/features/goals/adaptiveResult.test.ts
```

Expected: fail because `./adaptiveResult` does not exist.

- [ ] **Step 3: Implement the normalizer**

Create `agent-verse-frontend/src/features/goals/adaptiveResult.ts`:

```ts
export type AdaptiveColumnType = 'text' | 'link' | 'badge' | 'datetime' | 'number';

export type AdaptiveResultStatus = 'success' | 'failed' | 'empty' | 'partial';

export type AdaptivePrimaryView = 'table' | 'text' | 'json' | 'diagnostic';

export interface AdaptiveResultColumn {
  key: string;
  label: string;
  type: AdaptiveColumnType;
}

export interface AdaptiveResultTable {
  title: string;
  columns: AdaptiveResultColumn[];
  rows: Record<string, unknown>[];
}

export interface AdaptiveResultViewModel {
  status: AdaptiveResultStatus;
  title: string;
  summary?: string;
  metrics: Array<{ label: string; value: string | number | boolean }>;
  primaryView: AdaptivePrimaryView;
  table?: AdaptiveResultTable;
  text?: string;
  diagnostics: Array<{ label: string; value: string }>;
  raw: unknown;
}

export interface AdaptiveResultContext {
  toolName?: string;
  serverId?: string;
  success?: boolean;
  error?: unknown;
}

const ARRAY_KEYS = [
  'issues',
  'items',
  'results',
  'values',
  'nodes',
  'pull_requests',
  'tickets',
  'tasks',
  'users',
  'files',
  'messages',
] as const;

const METRIC_KEYS: Record<string, string> = {
  total: 'Total',
  total_count: 'Total',
  returned: 'Returned',
  max_results: 'Max results',
  maxResults: 'Max results',
  is_complete: 'Complete',
  isLast: 'Complete',
};

const COLUMN_ORDER = [
  'key',
  'id',
  'number',
  'identifier',
  'name',
  'title',
  'summary',
  'text',
  'status',
  'state',
  'priority',
  'type',
  'issue_type',
  'assignee',
  'owner',
  'author',
  'reporter',
  'created_by',
  'created',
  'created_at',
  'updated',
  'updated_at',
  'last_seen',
  'ts',
] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function humanize(value: string): string {
  return value
    .replace(/_/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/^./, (char) => char.toUpperCase());
}

function stringifyDiagnostic(value: unknown): string {
  if (value === undefined || value === null || value === '') return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (isRecord(value)) {
    const displayName = value.displayName ?? value.name ?? value.login ?? value.email;
    if (typeof displayName === 'string') return displayName;
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function findArray(value: unknown): { key: string; rows: Record<string, unknown>[] } | undefined {
  if (Array.isArray(value) && value.every(isRecord)) {
    return { key: 'results', rows: value };
  }
  if (!isRecord(value)) return undefined;
  for (const key of ARRAY_KEYS) {
    const child = value[key];
    if (Array.isArray(child) && child.every(isRecord)) {
      return { key, rows: child };
    }
  }
  for (const child of Object.values(value)) {
    const found = findArray(child);
    if (found) return found;
  }
  return undefined;
}

function extractMetrics(output: unknown, rowCount: number) {
  const metrics: Array<{ label: string; value: string | number | boolean }> = [];
  if (isRecord(output)) {
    for (const [key, label] of Object.entries(METRIC_KEYS)) {
      const value = output[key];
      if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
        metrics.push({ label, value });
      }
    }
  }
  if (!metrics.some((metric) => metric.label === 'Returned') && rowCount > 0) {
    metrics.push({ label: 'Returned', value: rowCount });
  }
  return metrics;
}

function normalizeRow(row: Record<string, unknown>): Record<string, unknown> {
  const normalized = { ...row };
  const state = row.state;
  const assignee = row.assignee;
  const author = row.author ?? row.user;
  const owner = row.owner;

  if (isRecord(state)) normalized.status = state.name ?? state.status ?? stringifyDiagnostic(state);
  if (isRecord(assignee)) normalized.assignee = assignee.displayName ?? assignee.name ?? stringifyDiagnostic(assignee);
  if (isRecord(author)) normalized.author = author.displayName ?? author.name ?? author.login ?? stringifyDiagnostic(author);
  if (isRecord(owner)) normalized.owner = owner.displayName ?? owner.name ?? owner.login ?? stringifyDiagnostic(owner);
  if (typeof row.html_url === 'string') normalized.url = row.html_url;
  if (typeof row.web_url === 'string') normalized.url = row.web_url;

  return normalized;
}

function columnType(key: string, sample: unknown): AdaptiveColumnType {
  if (['url', 'web_url', 'html_url', 'self'].includes(key)) return 'link';
  if (['status', 'state', 'priority', 'type', 'issue_type'].includes(key)) return 'badge';
  if (['created', 'created_at', 'updated', 'updated_at', 'last_seen', 'ts'].includes(key)) return 'datetime';
  if (typeof sample === 'number') return 'number';
  if (['key', 'id', 'number', 'identifier', 'name', 'title'].includes(key)) return 'link';
  return 'text';
}

function inferColumns(rows: Record<string, unknown>[]): AdaptiveResultColumn[] {
  const available = new Set(rows.flatMap((row) => Object.keys(row)));
  const ordered = [
    ...COLUMN_ORDER.filter((key) => available.has(key)),
    ...Array.from(available).filter((key) => !COLUMN_ORDER.includes(key as (typeof COLUMN_ORDER)[number])),
  ];
  return ordered
    .filter((key) => key !== 'url' && key !== 'html_url' && key !== 'web_url' && key !== 'self')
    .slice(0, 7)
    .map((key) => ({ key, label: humanize(key), type: columnType(key, rows[0]?.[key]) }));
}

function outputError(output: unknown, context: AdaptiveResultContext): string {
  if (context.error != null) return stringifyDiagnostic(context.error);
  if (isRecord(output) && output.error != null) return stringifyDiagnostic(output.error);
  return '';
}

function queryValue(output: unknown): string {
  if (!isRecord(output)) return '';
  return stringifyDiagnostic(output.query ?? output.jql ?? output.filter ?? output.q);
}

export function normalizeAdaptiveResult(
  output: unknown,
  context: AdaptiveResultContext = {}
): AdaptiveResultViewModel {
  const toolName = context.toolName || 'Tool output';
  const error = outputError(output, context);
  const failed = context.success === false || error.length > 0;
  const found = findArray(output);
  const rows = found?.rows.map(normalizeRow) ?? [];
  const metrics = extractMetrics(output, rows.length);

  if (failed) {
    return {
      status: 'failed',
      title: `${toolName} failed`,
      summary: error || 'The tool call failed.',
      metrics,
      primaryView: 'diagnostic',
      diagnostics: [
        { label: 'Tool', value: toolName },
        ...(context.serverId ? [{ label: 'Server', value: context.serverId }] : []),
        ...(error ? [{ label: 'Error', value: error }] : []),
      ],
      raw: output,
    };
  }

  if (found && rows.length === 0) {
    const query = queryValue(output);
    return {
      status: 'empty',
      title: `${toolName} returned no rows`,
      summary: 'The tool call succeeded, but no rows matched the request.',
      metrics,
      primaryView: 'diagnostic',
      diagnostics: [
        { label: 'Tool', value: toolName },
        ...(context.serverId ? [{ label: 'Server', value: context.serverId }] : []),
        ...(query ? [{ label: 'Query', value: query }] : []),
        { label: 'Result', value: 'No rows matched. Check query, permissions, project scope, or credentials.' },
      ],
      raw: output,
    };
  }

  if (found && rows.length > 0) {
    return {
      status: 'success',
      title: `${toolName} results`,
      summary: `Returned ${rows.length} row${rows.length === 1 ? '' : 's'}.`,
      metrics,
      primaryView: 'table',
      table: {
        title: humanize(found.key),
        columns: inferColumns(rows),
        rows,
      },
      diagnostics: [
        { label: 'Tool', value: toolName },
        ...(context.serverId ? [{ label: 'Server', value: context.serverId }] : []),
      ],
      raw: output,
    };
  }

  if (['string', 'number', 'boolean'].includes(typeof output)) {
    return {
      status: 'success',
      title: `${toolName} output`,
      metrics,
      primaryView: 'text',
      text: String(output),
      diagnostics: [{ label: 'Tool', value: toolName }],
      raw: output,
    };
  }

  return {
    status: 'success',
    title: `${toolName} output`,
    summary: 'Raw structured output is available below.',
    metrics,
    primaryView: 'json',
    diagnostics: [{ label: 'Tool', value: toolName }],
    raw: output,
  };
}
```

- [ ] **Step 4: Run normalizer tests to verify they pass**

Run:

```bash
npm run test -- src/features/goals/adaptiveResult.test.ts
```

Expected: all tests in `adaptiveResult.test.ts` pass.

- [ ] **Step 5: Checkpoint changes**

Run:

```bash
git diff -- src/features/goals/adaptiveResult.ts src/features/goals/adaptiveResult.test.ts
```

Expected: diff only contains the normalizer and its tests. If the user explicitly requested commits, commit these files with `feat: add adaptive result normalizer`; otherwise do not commit.

---

### Task 2: Add Adaptive Result Panel Component

**Files:**
- Create: `agent-verse-frontend/src/features/goals/components/AdaptiveResultPanel.tsx`
- Create: `agent-verse-frontend/src/features/goals/components/AdaptiveResultPanel.test.tsx`

- [ ] **Step 1: Write failing component tests**

Create `agent-verse-frontend/src/features/goals/components/AdaptiveResultPanel.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test } from 'vitest';
import { AdaptiveResultPanel } from './AdaptiveResultPanel';
import type { AdaptiveResultViewModel } from '../adaptiveResult';

const tableModel: AdaptiveResultViewModel = {
  status: 'success',
  title: 'jira_search_issues results',
  summary: 'Returned 1 row.',
  metrics: [
    { label: 'Total', value: 10 },
    { label: 'Returned', value: 1 },
  ],
  primaryView: 'table',
  table: {
    title: 'Issues',
    columns: [
      { key: 'key', label: 'Key', type: 'link' },
      { key: 'summary', label: 'Summary', type: 'text' },
      { key: 'status', label: 'Status', type: 'badge' },
    ],
    rows: [
      {
        key: 'OPP-34746',
        summary: 'Removed Logging in files in txn data service',
        status: 'To be deployed',
        url: 'https://jira.example.com/browse/OPP-34746',
      },
    ],
  },
  diagnostics: [{ label: 'Tool', value: 'jira_search_issues' }],
  raw: { total: 10, issues: [{ key: 'OPP-34746' }] },
};

describe('AdaptiveResultPanel', () => {
  test('renders summary metrics and table rows', () => {
    render(<AdaptiveResultPanel result={tableModel} />);

    expect(screen.getByRole('heading', { name: 'jira_search_issues results' })).toBeInTheDocument();
    expect(screen.getByText('Total')).toBeInTheDocument();
    expect(screen.getByText('10')).toBeInTheDocument();
    expect(screen.getByRole('table', { name: 'Issues' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'OPP-34746' })).toHaveAttribute(
      'href',
      'https://jira.example.com/browse/OPP-34746'
    );
    expect(screen.getByText('Removed Logging in files in txn data service')).toBeInTheDocument();
  });

  test('renders text output', () => {
    render(
      <AdaptiveResultPanel
        result={{
          status: 'success',
          title: 'shell_execute output',
          metrics: [],
          primaryView: 'text',
          text: 'All checks passed.',
          diagnostics: [],
          raw: 'All checks passed.',
        }}
      />
    );

    expect(screen.getByText('All checks passed.')).toBeInTheDocument();
  });

  test('renders diagnostics for empty or failed output', () => {
    render(
      <AdaptiveResultPanel
        result={{
          status: 'empty',
          title: 'jira_search_issues returned no rows',
          summary: 'The tool call succeeded, but no rows matched the request.',
          metrics: [{ label: 'Total', value: 0 }],
          primaryView: 'diagnostic',
          diagnostics: [
            { label: 'Tool', value: 'jira_search_issues' },
            { label: 'Query', value: 'assignee = "Nobody"' },
            { label: 'Result', value: 'No rows matched. Check query, permissions, project scope, or credentials.' },
          ],
          raw: { total: 0, issues: [] },
        }}
      />
    );

    expect(screen.getByText('Query')).toBeInTheDocument();
    expect(screen.getByText('assignee = "Nobody"')).toBeInTheDocument();
    expect(screen.getByText(/Check query, permissions/i)).toBeInTheDocument();
  });

  test('keeps raw output available in a disclosure', async () => {
    render(<AdaptiveResultPanel result={tableModel} />);

    await userEvent.click(screen.getByRole('button', { name: /raw output/i }));

    expect(screen.getByText(/"issues"/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
npm run test -- src/features/goals/components/AdaptiveResultPanel.test.tsx
```

Expected: fail because `AdaptiveResultPanel` does not exist.

- [ ] **Step 3: Implement `AdaptiveResultPanel`**

Create `agent-verse-frontend/src/features/goals/components/AdaptiveResultPanel.tsx`:

```tsx
import { AlertTriangle, CheckCircle, Database, FileJson } from 'lucide-react';
import type { AdaptiveColumnType, AdaptiveResultViewModel } from '../adaptiveResult';

type AdaptiveResultPanelProps = {
  result: AdaptiveResultViewModel;
  compact?: boolean;
};

function rawJson(value: unknown): string {
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function cellText(value: unknown): string {
  if (value === undefined || value === null || value === '') return '—';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function renderCell(
  value: unknown,
  type: AdaptiveColumnType,
  row: Record<string, unknown>
) {
  const text = cellText(value);
  const url = typeof row.url === 'string' ? row.url : undefined;

  if (type === 'badge') {
    return (
      <span className="inline-flex rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700 dark:bg-slate-800 dark:text-slate-200">
        {text}
      </span>
    );
  }

  if (type === 'link' && url && text !== '—') {
    return (
      <a className="break-words text-primary underline-offset-4 hover:underline" href={url}>
        {text}
      </a>
    );
  }

  if (type === 'datetime' && text !== '—') {
    return <time dateTime={text}>{text}</time>;
  }

  return <span className="break-words whitespace-pre-wrap">{text}</span>;
}

export function AdaptiveResultPanel({ result, compact = false }: AdaptiveResultPanelProps) {
  const isProblem = result.status === 'failed' || result.status === 'empty';
  const Icon = isProblem ? AlertTriangle : result.primaryView === 'json' ? FileJson : CheckCircle;
  const panelTone = isProblem
    ? 'border-amber-200 bg-amber-50 text-amber-950 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-100'
    : 'border-border bg-card text-card-foreground';

  return (
    <section className={`rounded-xl border ${panelTone} ${compact ? 'p-3' : 'p-4'} space-y-3`}>
      <div className="flex items-start gap-3">
        <Icon className={`mt-0.5 h-4 w-4 flex-shrink-0 ${isProblem ? 'text-amber-600' : 'text-green-600'}`} />
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold">{result.title}</h3>
          {result.summary && <p className="mt-1 text-xs text-muted-foreground">{result.summary}</p>}
        </div>
      </div>

      {result.metrics.length > 0 && (
        <dl className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {result.metrics.map((metric) => (
            <div key={`${metric.label}-${String(metric.value)}`} className="rounded-lg border bg-background/80 px-3 py-2">
              <dt className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                {metric.label}
              </dt>
              <dd className="mt-0.5 text-sm font-semibold">{String(metric.value)}</dd>
            </div>
          ))}
        </dl>
      )}

      {result.primaryView === 'diagnostic' && result.diagnostics.length > 0 && (
        <dl className="grid gap-2">
          {result.diagnostics.map((item) => (
            <div key={`${item.label}-${item.value}`} className="rounded-lg border bg-background/80 px-3 py-2">
              <dt className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                {item.label}
              </dt>
              <dd className="mt-0.5 break-words text-sm">{item.value}</dd>
            </div>
          ))}
        </dl>
      )}

      {result.primaryView === 'text' && result.text && (
        <p className="whitespace-pre-wrap break-words rounded-lg border bg-background/80 px-3 py-2 text-sm">
          {result.text}
        </p>
      )}

      {result.primaryView === 'table' && result.table && (
        <div className="overflow-x-auto rounded-lg border bg-background/80">
          <table aria-label={result.table.title} className="w-full text-sm">
            <thead className="bg-muted/40">
              <tr>
                {result.table.columns.map((column) => (
                  <th key={column.key} className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground" scope="col">
                    {column.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.table.rows.map((row, rowIndex) => (
                <tr key={rowIndex} className="border-t">
                  {result.table!.columns.map((column) => (
                    <td key={column.key} className="px-3 py-2 align-top">
                      {renderCell(row[column.key], column.type, row)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <details className="rounded-lg border bg-background/80 px-3 py-2">
        <summary className="flex cursor-pointer items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          <Database className="h-3 w-3" /> Raw output
        </summary>
        <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs text-muted-foreground">
          {rawJson(result.raw)}
        </pre>
      </details>
    </section>
  );
}
```

- [ ] **Step 4: Run component tests to verify they pass**

Run:

```bash
npm run test -- src/features/goals/components/AdaptiveResultPanel.test.tsx
```

Expected: all tests in `AdaptiveResultPanel.test.tsx` pass.

- [ ] **Step 5: Run normalizer and panel tests together**

Run:

```bash
npm run test -- src/features/goals/adaptiveResult.test.ts src/features/goals/components/AdaptiveResultPanel.test.tsx
```

Expected: both test files pass.

- [ ] **Step 6: Checkpoint changes**

Run:

```bash
git diff -- src/features/goals/adaptiveResult.ts src/features/goals/adaptiveResult.test.ts src/features/goals/components/AdaptiveResultPanel.tsx src/features/goals/components/AdaptiveResultPanel.test.tsx
```

Expected: diff only contains the adaptive model/normalizer/component and tests. If the user explicitly requested commits, commit these files with `feat: add adaptive result panel`; otherwise do not commit.

---

### Task 3: Render Adaptive Output in Goal Execution Rows

**Files:**
- Modify: `agent-verse-frontend/src/features/goals/GoalDetailPage.tsx`
- Modify: `agent-verse-frontend/src/features/goals/GoalDetailPage.test.tsx`

- [ ] **Step 1: Update the SSE mock test data**

Modify the default mocked `tool_call_complete` event near the top of `GoalDetailPage.test.tsx` so it has a realistic connector output:

```ts
{
  type: 'tool_call_complete',
  tool: 'jira.search',
  success: true,
  server_id: 'jira',
  output: {
    total: 1,
    issues: [
      {
        key: 'OPP-34746',
        summary: 'Removed Logging in files in txn data service',
        status: 'To be deployed',
        assignee: 'Abhay Dwivedi',
      },
    ],
  },
}
```

- [ ] **Step 2: Write failing execution row test**

Add this test inside the existing `describe('GoalDetailPage', () => { ... })` block in `GoalDetailPage.test.tsx`:

```tsx
test('renders expanded tool output as an adaptive table instead of raw JSON text', async () => {
  mockGoal('executing');
  renderGoalDetailPage();

  await userEvent.click(await screen.findByRole('button', { name: /jira\.search succeeded/i }));

  expect(screen.getByRole('table', { name: /issues/i })).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'OPP-34746' })).toBeInTheDocument();
  expect(screen.getByText('Removed Logging in files in txn data service')).toBeInTheDocument();
  expect(screen.getByText('Abhay Dwivedi')).toBeInTheDocument();
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run:

```bash
npm run test -- src/features/goals/GoalDetailPage.test.tsx -- -t "renders expanded tool output as an adaptive table"
```

Expected: fail because the row still renders a `<pre>` with stringified details, not an adaptive table.

- [ ] **Step 4: Modify `GoalDetailPage.tsx` imports**

Add these imports near the other local imports:

```ts
import { normalizeAdaptiveResult } from './adaptiveResult';
import { AdaptiveResultPanel } from './components/AdaptiveResultPanel';
```

- [ ] **Step 5: Add a helper for tool event names**

In `GoalDetailPage.tsx`, near `formatValue`, add:

```ts
function toolEventName(event: StreamGoalEvent): string {
  return readString(event.tool_name) ?? readString(event.tool) ?? 'Tool call';
}
```

Then in `eventSummary`, replace:

```ts
const toolName = readString(event.tool_name) ?? readString(event.tool) ?? "Tool call";
```

with:

```ts
const toolName = toolEventName(event);
```

- [ ] **Step 6: Replace expanded `<pre>` with adaptive rendering for tool events**

In `StepRow`, replace the current open block:

```tsx
{open && (
  <pre className="px-4 pb-3 text-xs overflow-x-auto whitespace-pre-wrap text-muted-foreground">
    {summary.details.length > 0 ? summary.details.join("\n") : JSON.stringify(event, null, 2)}
  </pre>
)}
```

with:

```tsx
{open && (
  <div className="px-4 pb-3 text-xs text-muted-foreground">
    {readString(event.type) === 'tool_call_complete' || readString(event.type) === 'tool_call_failed' ? (
      <AdaptiveResultPanel
        compact
        result={normalizeAdaptiveResult(event.output, {
          toolName: toolEventName(event),
          serverId: readString(event.server_id),
          success: typeof event.success === 'boolean' ? event.success : readString(event.type) !== 'tool_call_failed',
          error: event.error,
        })}
      />
    ) : (
      <pre className="overflow-x-auto whitespace-pre-wrap">
        {summary.details.length > 0 ? summary.details.join('\n') : JSON.stringify(event, null, 2)}
      </pre>
    )}
  </div>
)}
```

- [ ] **Step 7: Run targeted GoalDetailPage test**

Run:

```bash
npm run test -- src/features/goals/GoalDetailPage.test.tsx -- -t "renders expanded tool output as an adaptive table"
```

Expected: the new test passes.

- [ ] **Step 8: Run full GoalDetailPage test file**

Run:

```bash
npm run test -- src/features/goals/GoalDetailPage.test.tsx
```

Expected: all tests in `GoalDetailPage.test.tsx` pass.

- [ ] **Step 9: Checkpoint changes**

Run:

```bash
git diff -- src/features/goals/GoalDetailPage.tsx src/features/goals/GoalDetailPage.test.tsx
```

Expected: diff only integrates adaptive rendering into expanded execution rows and updates focused tests. If the user explicitly requested commits, commit these files with `feat: render adaptive tool output in execution rows`; otherwise do not commit.

---

### Task 4: Render Adaptive Output in Tool Call Inspector

**Files:**
- Modify: `agent-verse-frontend/src/components/execution/ToolCallInspector.tsx`
- Create or modify: `agent-verse-frontend/src/components/execution/ToolCallInspector.test.tsx`

- [ ] **Step 1: Check whether a test file exists**

Run:

```bash
test -f src/components/execution/ToolCallInspector.test.tsx && printf 'exists\n' || printf 'missing\n'
```

Expected: prints `exists` or `missing`. If `missing`, create the file in the next step.

- [ ] **Step 2: Write failing inspector tests**

Create or update `agent-verse-frontend/src/components/execution/ToolCallInspector.test.tsx` with:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test } from 'vitest';
import { ToolCallInspector } from './ToolCallInspector';

describe('ToolCallInspector', () => {
  test('renders selected tool output through the adaptive result panel', async () => {
    render(
      <ToolCallInspector
        toolEvents={[
          {
            type: 'tool_call_complete',
            tool_name: 'github_search_issues',
            server_id: 'github',
            success: true,
            arguments: { q: 'is:pr is:open' },
            output: {
              total_count: 1,
              items: [
                {
                  number: 1842,
                  title: 'Fix auth middleware',
                  state: 'open',
                  user: { login: 'octocat' },
                  html_url: 'https://github.com/acme/repo/pull/1842',
                },
              ],
            },
          },
        ]}
      />
    );

    await userEvent.click(screen.getByRole('button', { name: /github_search_issues/i }));

    expect(screen.getByRole('table', { name: /items/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '1842' })).toHaveAttribute(
      'href',
      'https://github.com/acme/repo/pull/1842'
    );
    expect(screen.getByText('Fix auth middleware')).toBeInTheDocument();
    expect(screen.getByText('octocat')).toBeInTheDocument();
  });

  test('renders failed selected tool output as diagnostics', async () => {
    render(
      <ToolCallInspector
        toolEvents={[
          {
            type: 'tool_call_complete',
            tool_name: 'jira_search_issues',
            server_id: 'jira',
            success: false,
            output: { error: 'HTTP 401: Unauthorized' },
            error: 'HTTP 401: Unauthorized',
          },
        ]}
      />
    );

    await userEvent.click(screen.getByRole('button', { name: /jira_search_issues/i }));

    expect(screen.getByRole('heading', { name: /jira_search_issues failed/i })).toBeInTheDocument();
    expect(screen.getByText('HTTP 401: Unauthorized')).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
npm run test -- src/components/execution/ToolCallInspector.test.tsx
```

Expected: fail because the inspector still renders output as raw `<pre>` text.

- [ ] **Step 4: Modify `ToolCallInspector.tsx` imports**

Add imports:

```ts
import { normalizeAdaptiveResult } from '@/features/goals/adaptiveResult';
import { AdaptiveResultPanel } from '@/features/goals/components/AdaptiveResultPanel';
```

- [ ] **Step 5: Replace raw output block with adaptive panel**

In `ToolDetail`, replace:

```tsx
{/* Output */}
{event.output !== undefined && (
  <div>
    <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1">
      Output
    </h4>
    <pre className="bg-muted rounded-lg px-3 py-2 text-xs overflow-x-auto whitespace-pre-wrap font-mono max-h-48">
      {formatJson(event.output)}
    </pre>
  </div>
)}
```

with:

```tsx
{/* Output */}
{event.output !== undefined && (
  <div>
    <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1">
      Output
    </h4>
    <AdaptiveResultPanel
      compact
      result={normalizeAdaptiveResult(event.output, {
        toolName: event.tool_name,
        serverId: event.server_id,
        success: event.success,
        error: event.error,
      })}
    />
  </div>
)}
```

Keep the separate error block below it. It is still useful when a connector returns no structured output but sets `event.error`.

- [ ] **Step 6: Run inspector tests to verify they pass**

Run:

```bash
npm run test -- src/components/execution/ToolCallInspector.test.tsx
```

Expected: all ToolCallInspector tests pass.

- [ ] **Step 7: Run execution component tests**

Run:

```bash
npm run test -- src/components/execution/ToolCallInspector.test.tsx src/components/execution/ExecutionTimeline.test.tsx
```

Expected: ToolCallInspector and ExecutionTimeline tests pass.

- [ ] **Step 8: Checkpoint changes**

Run:

```bash
git diff -- src/components/execution/ToolCallInspector.tsx src/components/execution/ToolCallInspector.test.tsx
```

Expected: diff only integrates adaptive output rendering into the inspector and adds focused tests. If the user explicitly requested commits, commit these files with `feat: render adaptive output in tool inspector`; otherwise do not commit.

---

### Task 5: Final Verification and Cleanup

**Files:**
- Verify: all modified frontend files.

- [ ] **Step 1: Run focused tests**

Run:

```bash
npm run test -- src/features/goals/adaptiveResult.test.ts src/features/goals/components/AdaptiveResultPanel.test.tsx src/features/goals/GoalDetailPage.test.tsx src/components/execution/ToolCallInspector.test.tsx src/components/execution/ExecutionTimeline.test.tsx
```

Expected: all focused tests pass.

- [ ] **Step 2: Run frontend typecheck**

Run:

```bash
npm run typecheck
```

Expected: TypeScript exits with code 0.

- [ ] **Step 3: Run frontend lint**

Run:

```bash
npm run lint
```

Expected: ESLint exits with code 0 or reports only pre-existing unrelated issues. If lint reports issues in files modified by this plan, fix them before continuing.

- [ ] **Step 4: Run frontend build**

Run:

```bash
npm run build
```

Expected: Vite build exits with code 0.

- [ ] **Step 5: Inspect final diff**

Run:

```bash
git diff -- src/features/goals/adaptiveResult.ts src/features/goals/adaptiveResult.test.ts src/features/goals/components/AdaptiveResultPanel.tsx src/features/goals/components/AdaptiveResultPanel.test.tsx src/features/goals/GoalDetailPage.tsx src/features/goals/GoalDetailPage.test.tsx src/components/execution/ToolCallInspector.tsx src/components/execution/ToolCallInspector.test.tsx
```

Expected: diff only contains adaptive connector result rendering work.

- [ ] **Step 6: Report verification evidence**

In the final implementation response, include:

```text
Implemented connector-agnostic adaptive result rendering.
Verification:
- npm run test -- ...: passed
- npm run typecheck: passed
- npm run lint: passed or documented pre-existing unrelated issues
- npm run build: passed
```

If the user explicitly requested commits, commit the final verified state with `feat: render adaptive connector results`; otherwise do not commit.
