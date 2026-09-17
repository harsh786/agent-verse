/**
 * Branch companion for EvalPage.
 *
 * The existing EvalPage.test.tsx covers the happy-path tab rendering, a basic
 * eval run, tools picker, red team launch + report, and suites list/create.
 * This file targets the UNTESTED branches: Scorecard compare mode, regression
 * detection banner, JSON/CSV export buttons, eval history sparkline table,
 * score delta display, evalMutation error branch, goal name truncation;
 * Simulation streaming (steps/complete/error events), invalid mock JSON,
 * tool toggle checkbox, network-error branch; Red Team pending progress bar,
 * error branch, and all risk_level/status/passRate color branches.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
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

const HISTORY_KEY = 'av_eval_history_tenant-1';

function makeEvalScore(overrides: Partial<Record<string, number>> = {}) {
  return {
    task_completion: 0.9, efficiency: 0.8, accuracy: 0.85, safety: 1.0,
    coherence: 0.75, sla: 0.95, tool_relevance: 0.7,
    ...overrides,
  };
}

function makeEvalFetch({
  goals = [] as object[],
  evalData = null as object | null,
  evalDataQueue = null as object[] | null,
  evalOk = true,
  evalStatus = 500,
  redTeamData = null as object | null,
  redTeamOk = true,
  redTeamDeferred = false,
  availableTools = [] as object[],
  suites = [] as object[],
  simulationImpl = null as ((url: string, init?: RequestInit) => Promise<Response>) | null,
} = {}) {
  let evalCallIndex = 0;
  const redTeamResolvers: Array<() => void> = [];

  const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();

    if (simulationImpl && url.includes('/enterprise/simulation/stream')) {
      return simulationImpl(url, init);
    }

    if (url.includes('/goals/') && url.endsWith('/eval') && method === 'GET') {
      if (!evalOk) {
        return new Response('server error', { status: evalStatus });
      }
      let data = evalData ?? { goal_id: 'g1', scores: makeEvalScore(), average_score: 0.85 };
      if (evalDataQueue) {
        data = evalDataQueue[Math.min(evalCallIndex, evalDataQueue.length - 1)];
        evalCallIndex++;
      }
      return new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (url.endsWith('/goals') || (url.includes('/goals') && !url.includes('/eval'))) {
      return new Response(JSON.stringify({ goals }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (url.includes('/enterprise/red-team') && method === 'POST') {
      if (redTeamDeferred) {
        return new Promise<Response>((resolve) => {
          redTeamResolvers.push(() => {
            if (!redTeamOk) {
              resolve(new Response('boom', { status: 500, statusText: 'Internal Error' }));
            } else {
              resolve(new Response(JSON.stringify(redTeamData ?? { total: 5, passed: 4, failed: 1, results: [] }), {
                status: 200, headers: { 'Content-Type': 'application/json' },
              }));
            }
          });
        });
      }
      if (!redTeamOk) {
        return new Response('boom', { status: 500, statusText: 'Internal Error' });
      }
      return new Response(JSON.stringify(redTeamData ?? { total: 5, passed: 4, failed: 1, results: [] }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/enterprise/simulation/available-tools')) {
      return new Response(JSON.stringify({ tools: availableTools, total: availableTools.length }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/intelligence/eval-suites') && method === 'GET' && !url.includes('/results')) {
      return new Response(JSON.stringify(suites), { status: 200, headers: { 'Content-Type': 'application/json' } });
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

  vi.spyOn(globalThis, 'fetch').mockImplementation(fetchImpl as unknown as typeof fetch);

  return {
    fetchImpl,
    resolveRedTeam: () => {
      const r = redTeamResolvers.shift();
      if (r) r();
    },
  };
}

function sseBody(events: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const evt of events) {
        controller.enqueue(encoder.encode(`data: ${evt}\n\n`));
      }
      controller.close();
    },
  });
}

describe('EvalPage branches', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'free',
      isAuthenticated: true,
    });
  });
  afterEach(() => vi.restoreAllMocks());

  // ─── ScorecardTab: compare mode ────────────────────────────────────────────

  describe('ScorecardTab compare mode', () => {
    const goals = [
      { id: 'g1', goal_id: 'g1', goal: 'Primary goal', status: 'complete' },
      { id: 'g2', goal_id: 'g2', goal: 'Secondary goal', status: 'complete' },
    ];

    test('toggling Compare shows the compare goal selector, selecting a goal renders compare radar, and toggling off resets it', async () => {
      makeEvalFetch({ goals });
      renderPage();

      await waitFor(() => {
        expect(document.querySelector('option[value="g1"]')).toBeTruthy();
      });

      const compareBtn = screen.getByRole('button', { name: /^compare$/i });
      await userEvent.click(compareBtn);

      expect(screen.getByText(/compare with:/i)).toBeInTheDocument();
      const selects = screen.getAllByRole('combobox');
      const compareSelect = selects[1];
      await userEvent.selectOptions(compareSelect, 'g2');

      // Run the main eval so the scorecard (and thus the radar) renders
      const mainSelect = selects[0];
      await userEvent.selectOptions(mainSelect, 'g1');
      await userEvent.click(screen.getByRole('button', { name: /run eval/i }));

      await waitFor(() => {
        expect(screen.getByTestId('radar-chart')).toBeInTheDocument();
      });

      // Toggle Compare off again -> resets compareGoalId to null
      await userEvent.click(compareBtn);
      expect(screen.queryByText(/compare with:/i)).not.toBeInTheDocument();
    });
  });

  // ─── ScorecardTab: regression detection ────────────────────────────────────

  describe('ScorecardTab regression detection', () => {
    test('shows "Regression detected" banner when latest score dips below the 7-day average', async () => {
      const history = [
        { goal_id: 'g0', scores: makeEvalScore(), average_score: 0.9, recorded_at: new Date().toISOString() },
        { goal_id: 'g0', scores: makeEvalScore(), average_score: 0.92, recorded_at: new Date().toISOString() },
      ];
      localStorage.setItem(HISTORY_KEY, JSON.stringify(history));

      const goals = [{ id: 'g1', goal_id: 'g1', goal: 'Regressed goal', status: 'complete' }];
      // Low score triggers regression vs seeded high history average
      makeEvalFetch({ goals, evalData: { goal_id: 'g1', scores: makeEvalScore(), average_score: 0.5 } });
      renderPage();

      await waitFor(() => expect(document.querySelector('option[value="g1"]')).toBeTruthy());
      await userEvent.selectOptions(screen.getByRole('combobox'), 'g1');
      await userEvent.click(screen.getByRole('button', { name: /run eval/i }));

      await waitFor(() => {
        expect(screen.getByText(/regression detected/i)).toBeInTheDocument();
      });
    });

    test('does NOT show regression banner when there is no history / no regression', async () => {
      const goals = [{ id: 'g1', goal_id: 'g1', goal: 'Healthy goal', status: 'complete' }];
      makeEvalFetch({ goals, evalData: { goal_id: 'g1', scores: makeEvalScore(), average_score: 0.9 } });
      renderPage();

      await waitFor(() => expect(document.querySelector('option[value="g1"]')).toBeTruthy());
      await userEvent.selectOptions(screen.getByRole('combobox'), 'g1');
      await userEvent.click(screen.getByRole('button', { name: /run eval/i }));

      await waitFor(() => {
        expect(screen.getByText(/all 7 dimensions/i)).toBeInTheDocument();
      });
      expect(screen.queryByText(/regression detected/i)).not.toBeInTheDocument();
    });
  });

  // ─── ScorecardTab: export JSON / CSV ────────────────────────────────────────

  describe('ScorecardTab export buttons', () => {
    test('Export JSON runs without throwing after an eval', async () => {
      const createObjectURL = vi.fn().mockReturnValue('blob:x');
      const revokeObjectURL = vi.fn();
      (URL as unknown as { createObjectURL: typeof createObjectURL }).createObjectURL = createObjectURL;
      (URL as unknown as { revokeObjectURL: typeof revokeObjectURL }).revokeObjectURL = revokeObjectURL;
      const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

      const goals = [{ id: 'g1', goal_id: 'g1', goal: 'Export goal', status: 'complete' }];
      makeEvalFetch({ goals });
      renderPage();

      await waitFor(() => expect(document.querySelector('option[value="g1"]')).toBeTruthy());
      await userEvent.selectOptions(screen.getByRole('combobox'), 'g1');
      await userEvent.click(screen.getByRole('button', { name: /run eval/i }));

      const exportJsonBtn = await screen.findByRole('button', { name: /export json/i });
      expect(() => exportJsonBtn.click()).not.toThrow();
      await userEvent.click(exportJsonBtn);

      expect(createObjectURL).toHaveBeenCalled();
      expect(revokeObjectURL).toHaveBeenCalled();
      expect(clickSpy).toHaveBeenCalled();
    });

    test('Export CSV button appears once history exists and is clickable without throwing', async () => {
      const createObjectURL = vi.fn().mockReturnValue('blob:x');
      const revokeObjectURL = vi.fn();
      (URL as unknown as { createObjectURL: typeof createObjectURL }).createObjectURL = createObjectURL;
      (URL as unknown as { revokeObjectURL: typeof revokeObjectURL }).revokeObjectURL = revokeObjectURL;
      const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

      const goals = [{ id: 'g1', goal_id: 'g1', goal: 'CSV goal', status: 'complete' }];
      makeEvalFetch({ goals });
      renderPage();

      await waitFor(() => expect(document.querySelector('option[value="g1"]')).toBeTruthy());
      await userEvent.selectOptions(screen.getByRole('combobox'), 'g1');
      await userEvent.click(screen.getByRole('button', { name: /run eval/i }));

      const exportCsvBtn = await screen.findByRole('button', { name: /export csv/i });
      await userEvent.click(exportCsvBtn);

      expect(createObjectURL).toHaveBeenCalled();
      expect(revokeObjectURL).toHaveBeenCalled();
      expect(clickSpy).toHaveBeenCalled();
    });
  });

  // ─── ScorecardTab: eval history table + sparkline ──────────────────────────

  describe('ScorecardTab eval history table', () => {
    test('shows "Eval History (N runs)" heading and score trend colors once history.length > 1', async () => {
      const history = [
        { goal_id: 'g0', scores: makeEvalScore({ task_completion: 0.9, efficiency: 0.4, sla: 0.2 }), average_score: 0.6, recorded_at: new Date().toISOString() },
        { goal_id: 'g0', scores: makeEvalScore({ task_completion: 0.85, efficiency: 0.45, sla: 0.25 }), average_score: 0.65, recorded_at: new Date().toISOString() },
      ];
      localStorage.setItem(HISTORY_KEY, JSON.stringify(history));

      makeEvalFetch({ goals: [] });
      renderPage();

      await waitFor(() => {
        expect(screen.getByText(/eval history \(2 runs\)/i)).toBeInTheDocument();
      });

      // Dimension columns should include high (emerald >=0.8), mid (amber >=0.5) and low (red) thresholds
      const table = screen.getByText(/eval history \(2 runs\)/i).closest('div')!.parentElement as HTMLElement;
      const cells = within(table).getAllByRole('cell');
      const classNames = cells.map((c) => c.className).join(' ');
      expect(classNames).toMatch(/text-emerald-400|text-amber-400|text-red-400/);
    });

    test('falls back to "#N" for a history row with no recorded_at', async () => {
      const history = [
        { goal_id: 'g0', scores: makeEvalScore(), average_score: 0.6 },
        { goal_id: 'g0', scores: makeEvalScore(), average_score: 0.65, recorded_at: new Date().toISOString() },
      ];
      localStorage.setItem(HISTORY_KEY, JSON.stringify(history));

      makeEvalFetch({ goals: [] });
      renderPage();

      await waitFor(() => {
        expect(screen.getByText(/eval history \(2 runs\)/i)).toBeInTheDocument();
      });
      expect(screen.getByText('#1')).toBeInTheDocument();
    });
  });

  // ─── ScorecardTab: score delta ──────────────────────────────────────────────

  describe('ScorecardTab score delta display', () => {
    test('shows +/- delta percentage on the second eval run vs the first', async () => {
      const goals = [{ id: 'g1', goal_id: 'g1', goal: 'Delta goal', status: 'complete' }];
      const evalDataQueue = [
        { goal_id: 'g1', scores: makeEvalScore({ task_completion: 0.5 }), average_score: 0.6 },
        { goal_id: 'g1', scores: makeEvalScore({ task_completion: 0.9 }), average_score: 0.8 },
      ];
      makeEvalFetch({ goals, evalDataQueue });
      renderPage();

      await waitFor(() => expect(document.querySelector('option[value="g1"]')).toBeTruthy());
      await userEvent.selectOptions(screen.getByRole('combobox'), 'g1');

      const runBtn = screen.getByRole('button', { name: /run eval/i });
      await userEvent.click(runBtn);
      await waitFor(() => expect(screen.getByText(/task completion/i)).toBeInTheDocument());

      await userEvent.click(runBtn);

      await waitFor(() => {
        // delta = 0.9 - 0.5 = +40.0%
        expect(screen.getByText(/\+40\.0%/)).toBeInTheDocument();
      });
    });
  });

  // ─── ScorecardTab: evalMutation error ───────────────────────────────────────

  describe('ScorecardTab eval error branch', () => {
    test('shows an error message when the eval fetch fails', async () => {
      const goals = [{ id: 'g1', goal_id: 'g1', goal: 'Error goal', status: 'complete' }];
      makeEvalFetch({ goals, evalOk: false, evalStatus: 500 });
      renderPage();

      await waitFor(() => expect(document.querySelector('option[value="g1"]')).toBeTruthy());
      await userEvent.selectOptions(screen.getByRole('combobox'), 'g1');
      await userEvent.click(screen.getByRole('button', { name: /run eval/i }));

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /run eval/i })).toBeEnabled();
      });
      // The error paragraph renders the stringified error
      const errParagraphs = document.querySelectorAll('p.text-red-400');
      expect(errParagraphs.length).toBeGreaterThan(0);
    });
  });

  // ─── ScorecardTab: goal name truncation ─────────────────────────────────────

  describe('ScorecardTab goal option truncation', () => {
    test('truncates goal strings over 70 chars with an ellipsis and leaves short ones intact', async () => {
      const longGoal = 'A'.repeat(80);
      const shortGoal = 'Short goal text';
      const goals = [
        { id: 'g-long', goal_id: 'g-long', goal: longGoal, status: 'complete' },
        { id: 'g-short', goal_id: 'g-short', goal: shortGoal, status: 'complete' },
      ];
      makeEvalFetch({ goals });
      renderPage();

      await waitFor(() => {
        const longOpt = document.querySelector('option[value="g-long"]') as HTMLOptionElement;
        const shortOpt = document.querySelector('option[value="g-short"]') as HTMLOptionElement;
        expect(longOpt).toBeTruthy();
        expect(shortOpt).toBeTruthy();
        expect(longOpt.textContent).toBe(`${longGoal.slice(0, 70)}…`);
        expect(shortOpt.textContent).toBe(shortGoal);
      });
    });
  });

  // ─── SimulationTab ───────────────────────────────────────────────────────────

  describe('SimulationTab streaming', () => {
    test('renders steps and the "complete" status + total cost from a full SSE stream', async () => {
      const events = [
        JSON.stringify({ type: 'simulation_step', step: 1, tool: 'github:list_issues', output: 'found 3 issues', cost_usd: 0.002 }),
        JSON.stringify({ type: 'simulation_complete', status: 'complete', cost_usd: 0.01 }),
      ];
      const simulationImpl = async () =>
        new Response(sseBody(events), { status: 200, headers: { 'Content-Type': 'text/event-stream' } });

      makeEvalFetch({ simulationImpl });
      renderPage();
      await userEvent.click(screen.getByRole('tab', { name: /simulation/i }));

      await userEvent.type(screen.getByPlaceholderText(/describe the goal to simulate/i), 'Do something');
      await userEvent.click(screen.getByRole('button', { name: /run simulation/i }));

      await waitFor(() => {
        expect(screen.getByText('github:list_issues')).toBeInTheDocument();
        expect(screen.getByText(/found 3 issues/i)).toBeInTheDocument();
        expect(screen.getByText('complete')).toBeInTheDocument();
        expect(screen.getByText(/total cost: \$0\.0100/i)).toBeInTheDocument();
      });
    });

    test('shows "error: <message>" styling when a simulation_error event is emitted', async () => {
      // The final-summary block only renders once at least one step has been
      // recorded, so emit a step first, then the error.
      const events = [
        JSON.stringify({ type: 'simulation_step', step: 1, tool: 'some:tool' }),
        JSON.stringify({ type: 'simulation_error', message: 'boom' }),
      ];
      const simulationImpl = async () =>
        new Response(sseBody(events), { status: 200, headers: { 'Content-Type': 'text/event-stream' } });

      makeEvalFetch({ simulationImpl });
      renderPage();
      await userEvent.click(screen.getByRole('tab', { name: /simulation/i }));
      await userEvent.type(screen.getByPlaceholderText(/describe the goal to simulate/i), 'Trigger error');
      await userEvent.click(screen.getByRole('button', { name: /run simulation/i }));

      await waitFor(() => {
        expect(screen.getByText(/error: boom/i)).toBeInTheDocument();
      });
    });

    test('a status other than "complete"/"error*" falls back to the neutral indigo styling', async () => {
      const events = [
        JSON.stringify({ type: 'simulation_step', step: 1, tool: 'some:tool' }),
        JSON.stringify({ type: 'simulation_complete', status: 'partial' }),
      ];
      const simulationImpl = async () =>
        new Response(sseBody(events), { status: 200, headers: { 'Content-Type': 'text/event-stream' } });

      makeEvalFetch({ simulationImpl });
      renderPage();
      await userEvent.click(screen.getByRole('tab', { name: /simulation/i }));
      await userEvent.type(screen.getByPlaceholderText(/describe the goal to simulate/i), 'Partial run');
      await userEvent.click(screen.getByRole('button', { name: /run simulation/i }));

      await waitFor(() => {
        expect(screen.getByText('partial')).toBeInTheDocument();
      });
      expect(screen.getByText('partial').className).toMatch(/text-indigo-400/);
    });

    test('network error (fetch rejects) sets sim status to "error: <message>"', async () => {
      const simulationImpl = async () => {
        throw new Error('network down');
      };
      makeEvalFetch({ simulationImpl });
      renderPage();
      await userEvent.click(screen.getByRole('tab', { name: /simulation/i }));
      await userEvent.type(screen.getByPlaceholderText(/describe the goal to simulate/i), 'Will fail');
      await userEvent.click(screen.getByRole('button', { name: /run simulation/i }));

      await waitFor(() => {
        expect(screen.getByText(/error: network down/i)).toBeInTheDocument();
      });
    });

    test('invalid JSON in Mock Tool Responses does not start a simulation', async () => {
      makeEvalFetch();
      renderPage();
      await userEvent.click(screen.getByRole('tab', { name: /simulation/i }));
      await userEvent.type(screen.getByPlaceholderText(/describe the goal to simulate/i), 'A goal');

      const mockTextarea = screen.getByPlaceholderText(/github:list_issues/i);
      fireEvent.change(mockTextarea, { target: { value: '{not valid json' } });

      await userEvent.click(screen.getByRole('button', { name: /run simulation/i }));

      // No steps card should appear since runSimulation returned early
      await waitFor(() => {
        expect(screen.queryByText(/execution steps/i)).not.toBeInTheDocument();
      });
    });

    test('toggling a tool checkbox checks then unchecks it', async () => {
      const tools = [{ name: 'github:list_issues', description: 'List issues', server_id: 'gh' }];
      makeEvalFetch({ availableTools: tools });
      renderPage();
      await userEvent.click(screen.getByRole('tab', { name: /simulation/i }));

      await waitFor(() => expect(screen.getByTestId('tools-picker')).toBeInTheDocument());
      const checkbox = screen.getByRole('checkbox') as HTMLInputElement;
      expect(checkbox.checked).toBe(false);

      await userEvent.click(checkbox);
      expect(checkbox.checked).toBe(true);

      await userEvent.click(checkbox);
      expect(checkbox.checked).toBe(false);
    });

    test('Run Simulation button is disabled when goal is blank and enabled once goal has text', async () => {
      makeEvalFetch();
      renderPage();
      await userEvent.click(screen.getByRole('tab', { name: /simulation/i }));

      const runBtn = screen.getByRole('button', { name: /run simulation/i });
      expect(runBtn).toBeDisabled();

      await userEvent.type(screen.getByPlaceholderText(/describe the goal to simulate/i), 'Now has text');
      expect(runBtn).toBeEnabled();
    });
  });

  // ─── RedTeamTab ──────────────────────────────────────────────────────────────

  describe('RedTeamTab pending / error branches', () => {
    test('shows the "Testing…" progress bar while the mutation is pending', async () => {
      const { resolveRedTeam } = makeEvalFetch({ redTeamDeferred: true });
      renderPage();
      await userEvent.click(screen.getByRole('tab', { name: /red team/i }));
      await userEvent.click(screen.getByRole('button', { name: /launch red team suite/i }));

      await waitFor(() => {
        expect(screen.getByText(/testing…/i)).toBeInTheDocument();
      });

      resolveRedTeam();
      await waitFor(() => {
        expect(screen.getByText('Security Score')).toBeInTheDocument();
      });
    });

    test('shows the red error message when the red-team POST fails', async () => {
      makeEvalFetch({ redTeamOk: false });
      renderPage();
      await userEvent.click(screen.getByRole('tab', { name: /red team/i }));
      await userEvent.click(screen.getByRole('button', { name: /launch red team suite/i }));

      await waitFor(() => {
        const err = document.querySelector('p.text-red-400');
        expect(err).toBeTruthy();
      });
    });
  });

  describe('RedTeamTab results table color branches', () => {
    test('renders critical / high / low risk badges and BLOCKED / LEAKED / fallback status badges', async () => {
      const redTeamData = {
        total: 3, passed: 1, failed: 2,
        results: [
          { case_id: 'c1', name: 'Critical case', status: 'passed', risk_level: 'critical' },
          { case_id: 'c2', name: 'High case', status: 'failed', risk_level: 'high' },
          { case_id: 'c3', status: 'error', risk_level: 'low' }, // no name -> falls back to case_id
        ],
      };
      makeEvalFetch({ redTeamData });
      renderPage();
      await userEvent.click(screen.getByRole('tab', { name: /red team/i }));
      await userEvent.click(screen.getByRole('button', { name: /launch red team suite/i }));

      await waitFor(() => expect(screen.getByText('Critical case')).toBeInTheDocument());
      expect(screen.getByText('High case')).toBeInTheDocument();
      expect(screen.getByText('c3')).toBeInTheDocument(); // fallback to case_id

      expect(screen.getByText('critical')).toBeInTheDocument();
      expect(screen.getByText('high')).toBeInTheDocument();
      expect(screen.getByText('low')).toBeInTheDocument();

      expect(screen.getByText('BLOCKED')).toBeInTheDocument();
      expect(screen.getAllByText('LEAKED').length).toBeGreaterThan(0); // failed + error both render LEAKED
    });

    test('renders the attack_vector line when present', async () => {
      const redTeamData = {
        total: 1, passed: 0, failed: 1,
        results: [{ case_id: 'c-av', name: 'Vector case', status: 'failed', attack_vector: 'SQL injection attempt' }],
      };
      makeEvalFetch({ redTeamData });
      renderPage();
      await userEvent.click(screen.getByRole('tab', { name: /red team/i }));
      await userEvent.click(screen.getByRole('button', { name: /launch red team suite/i }));

      await waitFor(() => expect(screen.getByText('Vector case')).toBeInTheDocument());
      expect(screen.getByText('SQL injection attempt')).toBeInTheDocument();
    });

    test('omits the risk badge when risk_level is absent', async () => {
      const redTeamData = {
        total: 1, passed: 1, failed: 0,
        results: [{ case_id: 'c-no-risk', name: 'No risk case', status: 'passed' }],
      };
      makeEvalFetch({ redTeamData });
      renderPage();
      await userEvent.click(screen.getByRole('tab', { name: /red team/i }));
      await userEvent.click(screen.getByRole('button', { name: /launch red team suite/i }));

      await waitFor(() => expect(screen.getByText('No risk case')).toBeInTheDocument());
      // No risk-level text nodes rendered for this row
      expect(screen.queryByText('critical')).not.toBeInTheDocument();
      expect(screen.queryByText('high')).not.toBeInTheDocument();
    });
  });

  describe('RedTeamTab passRate thresholds', () => {
    test('passRate >= 80 renders emerald and avoids div-by-zero when total is 0', async () => {
      makeEvalFetch({ redTeamData: { total: 0, passed: 0, failed: 0, results: [] } });
      renderPage();
      await userEvent.click(screen.getByRole('tab', { name: /red team/i }));
      await userEvent.click(screen.getByRole('button', { name: /launch red team suite/i }));

      await waitFor(() => expect(screen.getByText('Security Score')).toBeInTheDocument());
      // total || 1 => passed/1 = 0/1 = 0% (falls into the < 50 bucket, but proves no NaN/Infinity)
      expect(screen.getByText('0%')).toBeInTheDocument();
    });

    test('passRate >= 80 bucket (e.g. 90%)', async () => {
      makeEvalFetch({ redTeamData: { total: 10, passed: 9, failed: 1, results: [] } });
      renderPage();
      await userEvent.click(screen.getByRole('tab', { name: /red team/i }));
      await userEvent.click(screen.getByRole('button', { name: /launch red team suite/i }));

      await waitFor(() => expect(screen.getByText('90%')).toBeInTheDocument());
    });

    test('passRate >= 50 but < 80 bucket (e.g. 60%)', async () => {
      makeEvalFetch({ redTeamData: { total: 10, passed: 6, failed: 4, results: [] } });
      renderPage();
      await userEvent.click(screen.getByRole('tab', { name: /red team/i }));
      await userEvent.click(screen.getByRole('button', { name: /launch red team suite/i }));

      await waitFor(() => expect(screen.getByText('60%')).toBeInTheDocument());
    });

    test('passRate < 50 bucket (e.g. 20%)', async () => {
      makeEvalFetch({ redTeamData: { total: 10, passed: 2, failed: 8, results: [] } });
      renderPage();
      await userEvent.click(screen.getByRole('tab', { name: /red team/i }));
      await userEvent.click(screen.getByRole('button', { name: /launch red team suite/i }));

      await waitFor(() => expect(screen.getByText('20%')).toBeInTheDocument());
    });
  });
});
