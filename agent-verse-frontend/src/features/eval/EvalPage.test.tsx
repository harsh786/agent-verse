import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { EvalPage } from './EvalPage';

// Mock recharts to avoid SVG/canvas issues in jsdom
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

const ALL_7_DIMS = [
  'Task Completion', 'Efficiency', 'Accuracy',
  'Safety', 'Coherence', 'SLA', 'Tool Relevance',
];

function makeEvalFetch({
  goals = [] as object[],
  evalData = null as object | null,
  redTeamData = null as object | null,
  availableTools = [] as object[],
  suites = [] as object[],
} = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();

    if (url.includes('/goals/') && url.endsWith('/eval') && method === 'GET') {
      return new Response(
        JSON.stringify(evalData ?? {
          goal_id: 'g1',
          scores: { task_completion: 0.9, efficiency: 0.8, accuracy: 0.85, safety: 1.0, coherence: 0.75, sla: 0.95, tool_relevance: 0.7 },
          average_score: 0.85,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }
    if (url.endsWith('/goals') || (url.includes('/goals') && !url.includes('/eval'))) {
      return new Response(
        JSON.stringify({ goals }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }
    if (url.includes('/enterprise/red-team') && method === 'POST') {
      return new Response(
        JSON.stringify(redTeamData ?? { total: 5, passed: 4, failed: 1, results: [] }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }
    if (url.includes('/enterprise/simulation/available-tools')) {
      return new Response(
        JSON.stringify({ tools: availableTools, total: availableTools.length }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }
    if (url.includes('/intelligence/eval-suites') && method === 'GET' && !url.includes('/results')) {
      return new Response(
        JSON.stringify(suites),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }
    if (url.includes('/intelligence/eval-suites') && url.endsWith('/results')) {
      return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (url.includes('/intelligence/suggestions')) {
      return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (url.includes('/agents')) {
      return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

describe('EvalPage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'free',
      isAuthenticated: true,
    });
  });
  afterEach(() => vi.restoreAllMocks());

  test('renders page title', () => {
    makeEvalFetch();
    renderPage();
    expect(screen.getByText('Eval & Testing')).toBeInTheDocument();
  });

  test('shows all 4 tabs', () => {
    makeEvalFetch();
    renderPage();
    expect(screen.getByRole('tab', { name: /scorecard/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /simulation/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /red team/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /suites/i })).toBeInTheDocument();
  });

  test('Scorecard tab is active by default and shows goal selector', () => {
    makeEvalFetch();
    renderPage();
    expect(screen.getByText(/select a goal to evaluate/i)).toBeInTheDocument();
  });

  test('Scorecard tab shows run eval button', () => {
    makeEvalFetch();
    renderPage();
    expect(screen.getByRole('button', { name: /run eval/i })).toBeInTheDocument();
  });

  test('Scorecard shows all 7 dimension labels after eval runs', async () => {
    const goals = [{ id: 'g1', goal_id: 'g1', goal: 'Test goal', status: 'complete' }];
    makeEvalFetch({ goals });
    renderPage();

    // Wait for goals to load (the option appears asynchronously)
    await waitFor(() => {
      const select = screen.getByRole('combobox');
      expect(select).toBeInTheDocument();
      const option = document.querySelector('option[value="g1"]');
      expect(option).toBeTruthy();
    });

    const select = screen.getByRole('combobox');
    await userEvent.selectOptions(select, 'g1');

    const runBtn = screen.getByRole('button', { name: /run eval/i });
    await userEvent.click(runBtn);

    await waitFor(() => {
      for (const dim of ALL_7_DIMS) {
        expect(screen.getByText(dim)).toBeInTheDocument();
      }
    });
  });

  test('Scorecard shows 7 dimension names in empty state description', () => {
    makeEvalFetch();
    renderPage();
    // Empty state shows the 7 dimension list
    expect(screen.getByText(/7 dimensions/i)).toBeInTheDocument();
  });

  test('switches to Simulation tab', async () => {
    makeEvalFetch();
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /simulation/i }));
    expect(screen.getByPlaceholderText(/describe the goal to simulate/i)).toBeInTheDocument();
  });

  test('Simulation tab calls available-tools and shows tool picker when tools present', async () => {
    const tools = [
      { name: 'github:list_issues', description: 'List GitHub issues', server_id: 'gh' },
      { name: 'slack:send_message', description: 'Send Slack message', server_id: 'sl' },
    ];
    makeEvalFetch({ availableTools: tools });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /simulation/i }));
    await waitFor(() => {
      expect(screen.getByTestId('tools-picker')).toBeInTheDocument();
      expect(screen.getByText('github:list_issues')).toBeInTheDocument();
      expect(screen.getByText('slack:send_message')).toBeInTheDocument();
    });
  });

  test('Simulation tab shows empty tools message when none available', async () => {
    makeEvalFetch({ availableTools: [] });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /simulation/i }));
    await waitFor(() => {
      expect(screen.getByText(/no tools available/i)).toBeInTheDocument();
    });
  });

  test('switches to Red Team tab and shows launch button', async () => {
    makeEvalFetch();
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /red team/i }));
    expect(screen.getByRole('button', { name: /launch red team suite/i })).toBeInTheDocument();
  });

  test('Red Team tab shows report after run', async () => {
    const redTeamData = {
      total: 5, passed: 4, failed: 1,
      results: [{ case_id: 'c1', name: 'Prompt injection', status: 'passed', risk_level: 'high' }],
    };
    makeEvalFetch({ redTeamData });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /red team/i }));
    await userEvent.click(screen.getByRole('button', { name: /launch red team suite/i }));
    await waitFor(() => {
      expect(screen.getByText('Security Score')).toBeInTheDocument();
    });
  });

  test('switches to Suites tab', async () => {
    makeEvalFetch();
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /suites/i }));
    expect(screen.getByText('Eval Suites')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /create suite/i })).toBeInTheDocument();
  });

  test('Suites tab shows empty state when no suites', async () => {
    makeEvalFetch({ suites: [] });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /suites/i }));
    await waitFor(() => {
      expect(screen.getByText(/no eval suites yet/i)).toBeInTheDocument();
    });
  });

  test('Suites tab shows suite list when suites present', async () => {
    makeEvalFetch({
      suites: [{
        suite_id: 's1',
        name: 'Regression Suite',
        task_count: 5,
        created_at: new Date().toISOString(),
        description: 'Core regressions',
      }],
    });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /suites/i }));
    await waitFor(() => {
      expect(screen.getByText('Regression Suite')).toBeInTheDocument();
    });
  });

  test('Suites tab shows create suite form when button clicked', async () => {
    makeEvalFetch();
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /suites/i }));
    await userEvent.click(screen.getByRole('button', { name: /create suite/i }));
    expect(screen.getByRole('textbox', { name: /suite name/i })).toBeInTheDocument();
  });
});
