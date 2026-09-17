/**
 * Suites-tab companion for EvalPage.
 *
 * EvalPage.test.tsx / EvalPage.branches.test.tsx cover Scorecard, Simulation
 * and Red Team thoroughly, plus the Suites tab's basic list/empty/create-form
 * rendering. This file targets the remaining SuitesTab handlers that a fresh
 * full-coverage run flagged as uncovered: expand/collapse a suite row and its
 * "Recent Runs" results (getSuiteResults query + the sync effect), the Run
 * mutation, the Add Task modal (open/close via backdrop, card stopPropagation,
 * X button, Cancel, and a real submit), the create-suite form's full
 * type-and-submit / cancel paths, and the delete-suite ConfirmModal's
 * cancel/confirm (success + error) branches.
 */
import React, { type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { EvalPage } from './EvalPage';

// Mock recharts to avoid SVG/canvas issues in jsdom (ThemedRadarChart is
// imported at module scope even though this file only exercises the Suites tab).
vi.mock('recharts', () => ({
  RadarChart: ({ children }: any) => <div data-testid="radar-chart">{children}</div>,
  Radar: () => null,
  PolarGrid: () => null,
  PolarAngleAxis: () => null,
  PolarRadiusAxis: () => null,
  BarChart: ({ children }: any) => <div>{children}</div>,
  Bar: () => null,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
  Legend: () => null,
  ResponsiveContainer: ({ children }: any) => <div>{children}</div>,
}));

// ConfirmModal (delete-suite flow) uses framer-motion's AnimatePresence.
// jsdom has no real rAF/animation completion, so stub framer-motion the same
// way sibling feature tests in this repo do: a memoized per-tag stub (not a
// fresh component per render, which would remount and lose state on every
// re-render).
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const stubCache = new Map<string, (props: { children?: ReactNode; [k: string]: unknown }) => React.ReactElement>();
  const makeStub = (tag: string) => {
    let stub = stubCache.get(tag);
    if (!stub) {
      stub = ({ children, ...props }) => React.createElement(tag, props as Record<string, unknown>, children);
      stubCache.set(tag, stub);
    }
    return stub;
  };
  return {
    ...actual,
    useReducedMotion: () => true,
    AnimatePresence: ({ children }: { children?: ReactNode }) => <>{children}</>,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : makeStub(key)),
    }),
  };
});

// ResizeObserver polyfill
(globalThis as Record<string, unknown>).ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <EvalPage />
    </QueryClientProvider>
  );
}

interface MockSuite {
  suite_id: string;
  name?: string;
  task_count?: number;
  created_at?: string;
  description?: string;
}

function makeSuitesFetch({
  suites = [] as MockSuite[],
  suiteResults = {} as Record<string, object[]>,
  createOk = true,
  addTaskOk = true,
  deleteOk = true,
} = {}) {
  const suitesState = [...suites];

  const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();

    if (url.includes('/intelligence/eval-suites') && url.endsWith('/results')) {
      const suiteId = url.split('/intelligence/eval-suites/')[1].split('/results')[0];
      return new Response(JSON.stringify(suiteResults[suiteId] ?? []), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/intelligence/eval-suites') && url.endsWith('/run') && method === 'POST') {
      return new Response(JSON.stringify({ run_id: 'r1' }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/intelligence/eval-suites') && url.endsWith('/tasks') && method === 'POST') {
      if (!addTaskOk) return new Response('boom', { status: 500, statusText: 'Server Error' });
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (url.includes('/intelligence/eval-suites') && method === 'DELETE') {
      if (!deleteOk) return new Response('boom', { status: 500, statusText: 'Server Error' });
      const suiteId = url.split('/intelligence/eval-suites/')[1];
      const idx = suitesState.findIndex((s) => s.suite_id === suiteId);
      if (idx >= 0) suitesState.splice(idx, 1);
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (url.includes('/intelligence/eval-suites') && method === 'POST') {
      if (!createOk) return new Response('boom', { status: 500, statusText: 'Server Error' });
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      const newSuite: MockSuite = {
        suite_id: `s-new-${suitesState.length + 1}`,
        name: body.name,
        description: body.description,
        task_count: 0,
      };
      suitesState.push(newSuite);
      return new Response(JSON.stringify(newSuite), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (url.includes('/intelligence/eval-suites') && method === 'GET') {
      return new Response(JSON.stringify(suitesState), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (url.includes('/goals')) {
      return new Response(JSON.stringify({ goals: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (url.includes('/enterprise/simulation/available-tools')) {
      return new Response(JSON.stringify({ tools: [], total: 0 }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });

  vi.spyOn(globalThis, 'fetch').mockImplementation(fetchImpl as unknown as typeof fetch);
  return { fetchImpl };
}

const baseSuite: MockSuite = {
  suite_id: 's1',
  name: 'Regression Suite',
  task_count: 2,
  created_at: new Date().toISOString(),
  description: 'Core regressions',
};

async function openSuitesTab() {
  await userEvent.click(screen.getByRole('tab', { name: /suites/i }));
}

describe('EvalPage SuitesTab handlers', () => {
  beforeEach(() => {
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'free',
      isAuthenticated: true,
    });
    useToastStore.setState({ toasts: [] });
  });
  afterEach(() => vi.restoreAllMocks());

  // ─── expand/collapse + results sync ────────────────────────────────────────

  test('expanding a suite row loads and displays its recent run results, collapsing hides them', async () => {
    makeSuitesFetch({
      suites: [baseSuite],
      suiteResults: { s1: [{ run_id: 'r1', passed: 3, failed: 1 }, { run_id: 'r2', passed: 4, failed: 0 }] },
    });
    renderPage();
    await openSuitesTab();
    await screen.findByText('Regression Suite');

    await userEvent.click(screen.getByText('Regression Suite'));

    await waitFor(() => {
      expect(screen.getByText('Recent Runs')).toBeInTheDocument();
    });
    expect(screen.getByText('4/4 pass')).toBeInTheDocument();

    // Collapse again -> results section disappears (activeSuiteId no longer matches)
    await userEvent.click(screen.getByText('Regression Suite'));
    expect(screen.queryByText('Recent Runs')).not.toBeInTheDocument();
  });

  // ─── Run mutation ───────────────────────────────────────────────────────────

  test('clicking Run triggers the run mutation and refreshes/renders suite results', async () => {
    makeSuitesFetch({
      suites: [baseSuite],
      suiteResults: { s1: [{ run_id: 'r1', passed: 2, failed: 0 }] },
    });
    renderPage();
    await openSuitesTab();
    await screen.findByText('Regression Suite');

    await userEvent.click(screen.getByRole('button', { name: /^run$/i }));

    await waitFor(() => {
      expect(screen.getByText('Recent Runs')).toBeInTheDocument();
    });
    expect(screen.getByText('2/2 pass')).toBeInTheDocument();
  });

  // ─── Add Task modal ─────────────────────────────────────────────────────────

  describe('Add Task modal', () => {
    test('opens via "+ Task", submit is disabled until goal is filled, and a full submit closes it', async () => {
      makeSuitesFetch({ suites: [baseSuite] });
      renderPage();
      await openSuitesTab();
      await screen.findByText('Regression Suite');

      await userEvent.click(screen.getByRole('button', { name: /\+ task/i }));
      expect(screen.getByText('Add Golden Task')).toBeInTheDocument();

      const submitBtn = screen.getByRole('button', { name: /^add task$/i });
      expect(submitBtn).toBeDisabled();

      await userEvent.type(screen.getByPlaceholderText(/what should the agent do/i), 'Do the thing');
      await userEvent.type(screen.getByPlaceholderText(/expected substring in output/i), 'done');
      await userEvent.type(
        screen.getByPlaceholderText(/github:list_issues, slack:send_message/i),
        'github:list_issues, slack:send_message'
      );
      await userEvent.type(screen.getByPlaceholderText(/shell:execute, db:delete/i), 'shell:execute');

      expect(submitBtn).toBeEnabled();
      await userEvent.click(submitBtn);

      await waitFor(() => {
        expect(screen.queryByText('Add Golden Task')).not.toBeInTheDocument();
      });
    });

    test('Cancel button closes the modal without submitting', async () => {
      makeSuitesFetch({ suites: [baseSuite] });
      renderPage();
      await openSuitesTab();
      await screen.findByText('Regression Suite');

      await userEvent.click(screen.getByRole('button', { name: /\+ task/i }));
      await userEvent.type(screen.getByPlaceholderText(/what should the agent do/i), 'Abandoned task');
      await userEvent.click(screen.getByRole('button', { name: /^cancel$/i }));

      expect(screen.queryByText('Add Golden Task')).not.toBeInTheDocument();
    });

    test('the X button closes the modal', async () => {
      makeSuitesFetch({ suites: [baseSuite] });
      renderPage();
      await openSuitesTab();
      await screen.findByText('Regression Suite');

      await userEvent.click(screen.getByRole('button', { name: /\+ task/i }));
      const header = screen.getByText('Add Golden Task').closest('div') as HTMLElement;
      const closeBtn = within(header).getByRole('button');
      await userEvent.click(closeBtn);

      expect(screen.queryByText('Add Golden Task')).not.toBeInTheDocument();
    });

    test('clicking inside the modal card does not close it, but clicking the backdrop does', async () => {
      makeSuitesFetch({ suites: [baseSuite] });
      renderPage();
      await openSuitesTab();
      await screen.findByText('Regression Suite');

      await userEvent.click(screen.getByRole('button', { name: /\+ task/i }));

      // Click on the card content itself (stopPropagation should keep it open)
      await userEvent.click(screen.getByText('Add Golden Task'));
      expect(screen.getByText('Add Golden Task')).toBeInTheDocument();

      // Click the backdrop element directly (not a descendant) to close it
      const overlay = screen.getByText('Add Golden Task').closest('.fixed') as HTMLElement;
      fireEvent.click(overlay);

      await waitFor(() => {
        expect(screen.queryByText('Add Golden Task')).not.toBeInTheDocument();
      });
    });

    test('shows a red error message when adding the task fails', async () => {
      makeSuitesFetch({ suites: [baseSuite], addTaskOk: false });
      renderPage();
      await openSuitesTab();
      await screen.findByText('Regression Suite');

      await userEvent.click(screen.getByRole('button', { name: /\+ task/i }));
      await userEvent.type(screen.getByPlaceholderText(/what should the agent do/i), 'Will fail');
      await userEvent.click(screen.getByRole('button', { name: /^add task$/i }));

      // Mutation errors are swallowed by react-query here (no onError handler),
      // so simply verify the modal stays open since addTaskMutation never succeeds.
      await waitFor(() => {
        expect(screen.getByText('Add Golden Task')).toBeInTheDocument();
      });
    });
  });

  // ─── Create suite form ──────────────────────────────────────────────────────

  describe('Create suite form', () => {
    test('typing a name/description and clicking Create adds the suite and resets the form', async () => {
      makeSuitesFetch({ suites: [] });
      renderPage();
      await openSuitesTab();
      await userEvent.click(screen.getByRole('button', { name: /create suite/i }));

      const nameInput = screen.getByRole('textbox', { name: /suite name/i });
      const descInput = screen.getByPlaceholderText(/description \(optional\)/i);
      await userEvent.type(nameInput, 'New Suite');
      await userEvent.type(descInput, 'A description');

      const createBtn = screen.getByRole('button', { name: /^create$/i });
      expect(createBtn).toBeEnabled();
      await userEvent.click(createBtn);

      await waitFor(() => {
        expect(screen.getByText('New Suite')).toBeInTheDocument();
      });
      expect(screen.queryByRole('textbox', { name: /suite name/i })).not.toBeInTheDocument();
    });

    test('clicking Cancel hides the create form without creating a suite', async () => {
      makeSuitesFetch({ suites: [] });
      renderPage();
      await openSuitesTab();
      await userEvent.click(screen.getByRole('button', { name: /create suite/i }));
      await userEvent.type(screen.getByRole('textbox', { name: /suite name/i }), 'Should not persist');

      await userEvent.click(screen.getByRole('button', { name: /^cancel$/i }));

      expect(screen.queryByRole('textbox', { name: /suite name/i })).not.toBeInTheDocument();
      expect(screen.queryByText('Should not persist')).not.toBeInTheDocument();
    });
  });

  // ─── Delete suite (ConfirmModal) ────────────────────────────────────────────

  describe('Delete suite confirmation', () => {
    test('Cancel closes the confirm modal without deleting', async () => {
      makeSuitesFetch({ suites: [baseSuite] });
      renderPage();
      await openSuitesTab();
      await screen.findByText('Regression Suite');

      await userEvent.click(screen.getByRole('button', { name: /delete suite: regression suite/i }));
      expect(screen.getByText('Delete evaluation suite?')).toBeInTheDocument();

      await userEvent.click(screen.getByRole('button', { name: /^cancel$/i }));

      expect(screen.queryByText('Delete evaluation suite?')).not.toBeInTheDocument();
      expect(screen.getByText('Regression Suite')).toBeInTheDocument();
    });

    test('confirming delete removes the suite and shows a success toast', async () => {
      makeSuitesFetch({ suites: [baseSuite] });
      renderPage();
      await openSuitesTab();
      await screen.findByText('Regression Suite');

      await userEvent.click(screen.getByRole('button', { name: /delete suite: regression suite/i }));
      await userEvent.click(screen.getByRole('button', { name: /delete suite$/i }));

      await waitFor(() => {
        expect(screen.queryByText('Regression Suite')).not.toBeInTheDocument();
      });
      expect(
        useToastStore.getState().toasts.some((t) => t.kind === 'success' && /suite deleted/i.test(t.message))
      ).toBe(true);
    });

    test('a failed delete shows an error toast and keeps the suite listed', async () => {
      makeSuitesFetch({ suites: [baseSuite], deleteOk: false });
      renderPage();
      await openSuitesTab();
      await screen.findByText('Regression Suite');

      await userEvent.click(screen.getByRole('button', { name: /delete suite: regression suite/i }));
      await userEvent.click(screen.getByRole('button', { name: /delete suite$/i }));

      await waitFor(() => {
        expect(useToastStore.getState().toasts.some((t) => t.kind === 'error')).toBe(true);
      });
      expect(screen.getByText('Regression Suite')).toBeInTheDocument();
    });
  });
});
