import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { AgentLabPage } from './AgentLabPage';

// ── Mocks ─────────────────────────────────────────────────────────────────────

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <AgentLabPage />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

function setupDefaultFetch() {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
    const u = String(url);
    if (u.includes('/agents')) {
      return new Response(
        JSON.stringify([{ agent_id: 'a1', name: 'Agent Alpha', autonomy_mode: 'manual' }]),
        { status: 200 }
      );
    }
    if (u.includes('/intelligence/eval-suites') && !u.includes('/results') && !u.includes('/run')) {
      return new Response(
        JSON.stringify([
          { suite_id: 's1', name: 'Suite One', task_count: 5, created_at: '2026-01-01' },
          { suite_id: 's2', name: 'Suite Two', task_count: 3, created_at: '2026-01-02' },
        ]),
        { status: 200 }
      );
    }
    if (u.includes('/eval-suites/') && u.includes('/results')) {
      return new Response(
        JSON.stringify([
          {
            run_id: 'run1',
            suite_id: 's1',
            overall_score: 0.85,
            passed: 4,
            failed: 1,
            completed_at: '2026-01-01',
          },
        ]),
        { status: 200 }
      );
    }
    if (u.includes('/governance/simulate')) {
      return new Response(
        JSON.stringify({
          summary: { risk_level: 'low', tools_required: 2 },
          policy_checks: [{ tool: 'jira:search', result: 'allow' }],
        }),
        { status: 200 }
      );
    }
    if (
      u.includes('/intelligence/prompt-variants') &&
      !u.includes('/promote') &&
      !u.includes('/report') &&
      !u.match(/\/intelligence\/prompt-variants\/[^/]+$/)
    ) {
      return new Response(
        JSON.stringify([
          {
            id: 'v1',
            key: 'planner',
            name: 'Control Planner',
            prompt_text: 'You are a planner.',
            is_control: true,
            run_count: 10,
            mean_score: 0.75,
            p95_score: 0.9,
            promoted_at: null,
          },
        ]),
        { status: 200 }
      );
    }
    if (u.includes('available-tools')) {
      return new Response(JSON.stringify({ tools: [], total: 0 }), { status: 200 });
    }
    if (u.includes('/red-team')) {
      return new Response(
        JSON.stringify({
          report_id: 'rt1',
          total: 3,
          passed: 2,
          failed: 1,
          run_at: '2026-01-01',
          cases_run: 3,
          cases_passed: 2,
          cases_failed: 1,
          results: [
            { case: 'prompt_injection', passed: true },
            { case: 'jailbreak_attempt', passed: true },
            { case: 'data_exfil', passed: false },
          ],
        }),
        { status: 201 }
      );
    }
    return new Response('{}', { status: 200 });
  });
}

beforeEach(() => {
  useAuthStore.setState({
    apiKey: 'test-key',
    tenantId: 't',
    plan: 'free',
    isAuthenticated: true,
  });
  useToastStore.setState({ toasts: [] });
  setupDefaultFetch();
});

afterEach(() => vi.restoreAllMocks());

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('AgentLabPage', () => {
  test('renders all 4 tabs', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /agent lab/i })).toBeDefined();
    });
    const tabs = screen.getAllByRole('tab');
    const labels = tabs.map((t) => t.textContent ?? '');
    expect(labels.some((l) => l.includes('Pre-Flight'))).toBe(true);
    expect(labels.some((l) => l.includes('Live Sim'))).toBe(true);
    expect(labels.some((l) => l.includes('Prompt Lab'))).toBe(true);
    expect(labels.some((l) => l.includes('Score'))).toBe(true);
  });

  test('pre-flight tab loads governance results on button click', async () => {
    renderPage();

    const textarea = await waitFor(() =>
      screen.getByPlaceholderText(/describe the goal to pre-flight/i)
    );
    fireEvent.change(textarea, { target: { value: 'Check Jira issues' } });
    fireEvent.click(screen.getByText('Run Governance Check'));

    await waitFor(() => {
      expect(screen.getByText('Governance Analysis')).toBeDefined();
    });
  });

  test('live sim tab fires SSE fetch on run', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: /live sim/i }));

    const textarea = await waitFor(() =>
      screen.getByPlaceholderText(/goal to simulate/i)
    );
    fireEvent.change(textarea, { target: { value: 'Run Jira simulation' } });

    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            'data: {"type":"step_started","step_number":1,"description":"Step one"}\n\n'
          )
        );
        controller.close();
      },
    });

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
      if (String(url).includes('simulation/stream')) {
        return new Response(null, { status: 200, body: stream } as ResponseInit);
      }
      if (String(url).includes('available-tools')) {
        return new Response(JSON.stringify({ tools: [], total: 0 }), { status: 200 });
      }
      return new Response('{}', { status: 200 });
    });

    // There could be multiple "Run Simulation" buttons; click the first
    const runBtns = screen.getAllByText('Run Simulation');
    fireEvent.click(runBtns[0]);

    await waitFor(() => {
      const calls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.map(
        (c: unknown[]) => String(c[0])
      );
      expect(calls.some((u) => u.includes('simulation'))).toBe(true);
    });
  });

  test('prompt lab tab loads variants from API', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: /prompt lab/i }));

    await waitFor(() => {
      expect(screen.getByText('Control Planner')).toBeDefined();
      expect(screen.getByText('CONTROL')).toBeDefined();
    });
  });

  test('create variant button calls POST /intelligence/prompt-variants', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: /prompt lab/i }));

    await waitFor(() => screen.getByText('Add Challenger Variant'));
    fireEvent.click(screen.getByText('Add Challenger Variant'));

    const nameInput = await waitFor(() =>
      screen.getByPlaceholderText(/variant name/i)
    );
    fireEvent.change(nameInput, { target: { value: 'Challenger Variant' } });

    const promptInput = screen.getByPlaceholderText(/prompt text for this variant/i);
    fireEvent.change(promptInput, { target: { value: 'Be concise and direct.' } });

    const mockFetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, opts) => {
      const u = String(url);
      const method = (opts as RequestInit)?.method ?? 'GET';
      if (u.includes('/intelligence/prompt-variants') && method === 'POST') {
        return new Response(
          JSON.stringify({
            id: 'v2',
            key: 'planner',
            name: 'Challenger Variant',
            prompt_text: 'Be concise and direct.',
            is_control: false,
            run_count: 0,
            mean_score: null,
            p95_score: null,
            promoted_at: null,
          }),
          { status: 201 }
        );
      }
      if (u.includes('/intelligence/prompt-variants')) {
        return new Response(JSON.stringify([]), { status: 200 });
      }
      return new Response('{}', { status: 200 });
    });

    fireEvent.click(screen.getByText('Create'));

    await waitFor(() => {
      const postCalls = mockFetchSpy.mock.calls.filter(
        (c: unknown[]) => String(c[0]).includes('/intelligence/prompt-variants')
      );
      expect(postCalls.length).toBeGreaterThan(0);
    });
  });

  test('promote variant calls POST /intelligence/prompt-variants/{id}/promote', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, opts) => {
      const u = String(url);
      const method = (opts as RequestInit)?.method ?? 'GET';
      if (u.includes('/intelligence/prompt-variants') && u.includes('/promote')) {
        return new Response(
          JSON.stringify({ id: 'v2', key: 'planner', promoted: true, promoted_at: '2026-01-01T00:00:00Z' }),
          { status: 200 }
        );
      }
      if (u.includes('/intelligence/prompt-variants') && method === 'GET') {
        return new Response(
          JSON.stringify([
            {
              id: 'v1',
              key: 'planner',
              name: 'Control',
              prompt_text: 'Control',
              is_control: true,
              run_count: 20,
              mean_score: 0.70,
              p95_score: 0.85,
              promoted_at: null,
            },
            {
              id: 'v2',
              key: 'planner',
              name: 'Challenger',
              prompt_text: 'Challenger',
              is_control: false,
              run_count: 15,
              mean_score: 0.82,
              p95_score: 0.95,
              promoted_at: null,
            },
          ]),
          { status: 200 }
        );
      }
      return new Response('{}', { status: 200 });
    });

    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: /prompt lab/i }));

    const promoteButton = await waitFor(() => screen.getByText('Promote'));
    fireEvent.click(promoteButton);

    await waitFor(() => {
      const calls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.map(
        (c: unknown[]) => String(c[0])
      );
      expect(calls.some((u) => u.includes('/promote'))).toBe(true);
    });
  });

  test('score tab uses selected suite dropdown, not hardcoded suites[0]', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: /score/i }));

    const suiteSelect = await waitFor(() =>
      screen.getByRole('combobox', { name: /select eval suite/i })
    );
    expect(suiteSelect).toBeDefined();

    await waitFor(() => {
      expect(screen.getByText('Suite One')).toBeDefined();
      expect(screen.getByText('Suite Two')).toBeDefined();
    });

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('/eval-suites/s2/results')) {
        return new Response(
          JSON.stringify([
            {
              run_id: 'run2',
              suite_id: 's2',
              overall_score: 0.92,
              passed: 3,
              failed: 0,
              completed_at: '2026-01-02',
            },
          ]),
          { status: 200 }
        );
      }
      return new Response(JSON.stringify([]), { status: 200 });
    });

    fireEvent.change(suiteSelect, { target: { value: 's2' } });

    await waitFor(() => {
      const calls = fetchSpy.mock.calls.map((c: unknown[]) => String(c[0]));
      expect(calls.some((u) => u.includes('/s2/results'))).toBe(true);
    });
  });

  test('red team runs and shows real results on button click', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: /score/i }));

    const rtButton = await waitFor(() => {
      const btns = screen.getAllByText(/Run Red Team/i);
      // Pick the button element (not any heading text)
      return btns.find((el) => el.tagName === 'BUTTON') ?? btns[0];
    });
    fireEvent.click(rtButton);

    await waitFor(() => {
      // After results load, security score % or BLOCKED/LEAKED badges appear
      const content = document.body.textContent ?? '';
      expect(
        content.includes('%') || content.includes('BLOCKED') || content.includes('security score')
      ).toBe(true);
    });
  });

  // ── Pre-Flight: extra branches ────────────────────────────────────────────

  test('pre-flight buttons are no-ops when goal is blank', async () => {
    renderPage();
    await waitFor(() => screen.getByText('Run Governance Check'));

    const fetchSpy = globalThis.fetch as ReturnType<typeof vi.fn>;
    const callsBefore = fetchSpy.mock.calls.length;

    fireEvent.click(screen.getByText('Run Governance Check'));
    fireEvent.click(screen.getByText('Preview Plan (Dry Run)'));

    // Buttons are disabled with blank goal, so no new network calls happen.
    expect(fetchSpy.mock.calls.length).toBe(callsBefore);
  });

  test('pre-flight governance check shows error toast on failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('/governance/simulate')) {
        return new Response('boom', { status: 500 });
      }
      if (u.includes('/agents')) {
        return new Response(JSON.stringify([]), { status: 200 });
      }
      throw new Error('network down');
    });

    renderPage();
    const textarea = await waitFor(() =>
      screen.getByPlaceholderText(/describe the goal to pre-flight/i)
    );
    fireEvent.change(textarea, { target: { value: 'Check Jira issues' } });
    fireEvent.click(screen.getByText('Run Governance Check'));

    await waitFor(() => {
      expect(
        useToastStore.getState().toasts.some((t) => t.message.includes('Governance check failed'))
      ).toBe(true);
    });
  });

  test('pre-flight dry-run preview plan renders result and selecting an agent works', async () => {
    renderPage();
    const textarea = await waitFor(() =>
      screen.getByPlaceholderText(/describe the goal to pre-flight/i)
    );
    fireEvent.change(textarea, { target: { value: 'Deploy the app' } });

    const agentSelect = await waitFor(() =>
      screen.getByDisplayValue('— Any agent —')
    );
    fireEvent.change(agentSelect, { target: { value: 'a1' } });

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('/goals')) {
        return new Response(JSON.stringify({ goal_id: 'g1', steps: ['step 1'] }), { status: 200 });
      }
      return new Response('{}', { status: 200 });
    });

    fireEvent.click(screen.getByText('Preview Plan (Dry Run)'));

    await waitFor(() => {
      expect(screen.getByText('Plan Preview')).toBeDefined();
    });
  });

  test('pre-flight dry-run shows error toast on failure', async () => {
    renderPage();
    const textarea = await waitFor(() =>
      screen.getByPlaceholderText(/describe the goal to pre-flight/i)
    );
    fireEvent.change(textarea, { target: { value: 'Deploy the app' } });

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('/goals')) {
        throw new Error('submit failed');
      }
      return new Response('{}', { status: 200 });
    });

    fireEvent.click(screen.getByText('Preview Plan (Dry Run)'));

    await waitFor(() => {
      expect(
        useToastStore.getState().toasts.some((t) => t.message.includes('Dry-run failed'))
      ).toBe(true);
    });
  });

  test('pre-flight governance result renders require_approval and deny policy checks', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('/governance/simulate')) {
        return new Response(
          JSON.stringify({
            summary: { risk_level: 'high' },
            policy_checks: [
              { tool: 'deploy:prod', result: 'require_approval' },
              { tool: 'db:drop', result: 'deny' },
            ],
          }),
          { status: 200 }
        );
      }
      return new Response(JSON.stringify([]), { status: 200 });
    });

    renderPage();
    const textarea = await waitFor(() =>
      screen.getByPlaceholderText(/describe the goal to pre-flight/i)
    );
    fireEvent.change(textarea, { target: { value: 'Deploy to prod' } });
    fireEvent.click(screen.getByText('Run Governance Check'));

    await waitFor(() => {
      expect(screen.getByText('REQUIRE_APPROVAL')).toBeDefined();
      expect(screen.getByText('DENY')).toBeDefined();
    });
  });

  // ── Live Simulation: extra branches ────────────────────────────────────────

  test('mock tools builder can add, edit, and remove a tool', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: /live sim/i }));

    await waitFor(() => screen.getByText('Add Tool'));
    fireEvent.click(screen.getByText('Add Tool'));

    const nameInput = await waitFor(() => screen.getByPlaceholderText('tool_name'));
    fireEvent.change(nameInput, { target: { value: 'my_tool' } });

    const outputInput = screen.getByPlaceholderText('{"result": "mock output"}');
    fireEvent.change(outputInput, { target: { value: '{"result": "custom"}' } });

    expect((nameInput as HTMLInputElement).value).toBe('my_tool');

    const removeBtn = screen.getByLabelText('Remove tool my_tool');
    fireEvent.click(removeBtn);

    await waitFor(() => {
      expect(screen.queryByPlaceholderText('tool_name')).not.toBeInTheDocument();
    });
    expect(screen.getByText(/no custom tools/i)).toBeDefined();
  });

  test('available tools checkbox toggles and is included in the simulation payload', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('available-tools')) {
        return new Response(
          JSON.stringify({ tools: [{ name: 'jira_search' }], total: 1 }),
          { status: 200 }
        );
      }
      return new Response('{}', { status: 200 });
    });

    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: /live sim/i }));

    const checkbox = await waitFor(() => screen.getByRole('checkbox'));
    fireEvent.click(checkbox);
    expect((checkbox as HTMLInputElement).checked).toBe(true);
    fireEvent.click(checkbox);
    expect((checkbox as HTMLInputElement).checked).toBe(false);
    fireEvent.click(checkbox);

    const textarea = screen.getByPlaceholderText(/goal to simulate/i);
    fireEvent.change(textarea, { target: { value: 'Search jira' } });

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('simulation/stream')) {
        return new Response(null, { status: 500 });
      }
      if (u.includes('/enterprise/simulation') && !u.includes('stream')) {
        return new Response(
          JSON.stringify({ steps: [{ step: 'do it', tool: 'jira_search', output: 'ok' }] }),
          { status: 200 }
        );
      }
      return new Response('{}', { status: 200 });
    });

    fireEvent.click(screen.getAllByText('Run Simulation')[0]);

    await waitFor(() => {
      const streamCall = fetchSpy.mock.calls.find((c) => String(c[0]).includes('simulation/stream'));
      expect(streamCall).toBeDefined();
      const body = JSON.parse(String((streamCall?.[1] as RequestInit)?.body));
      expect(body.mock_tools).toHaveProperty('jira_search');
    });
  });

  test('live sim falls back to non-streaming run when stream response is not ok', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: /live sim/i }));

    const textarea = await waitFor(() => screen.getByPlaceholderText(/goal to simulate/i));
    fireEvent.change(textarea, { target: { value: 'Fallback goal' } });

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('simulation/stream')) {
        return new Response(null, { status: 500 });
      }
      if (u.includes('/enterprise/simulation')) {
        return new Response(
          JSON.stringify({ steps: [{ step: 'fallback step', tool: 'x', output: 'y' }] }),
          { status: 200 }
        );
      }
      return new Response('{}', { status: 200 });
    });

    fireEvent.click(screen.getAllByText('Run Simulation')[0]);

    await waitFor(() => {
      expect(screen.getByText('fallback step')).toBeDefined();
    });
  });

  test('live sim stop button aborts the running simulation', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: /live sim/i }));

    const textarea = await waitFor(() => screen.getByPlaceholderText(/goal to simulate/i));
    fireEvent.change(textarea, { target: { value: 'Long running goal' } });

    let rejectFn: (e: Error) => void = () => {};
    vi.spyOn(globalThis, 'fetch').mockImplementation((_, opts) => {
      const signal = (opts as RequestInit | undefined)?.signal;
      return new Promise((_resolve, reject) => {
        rejectFn = reject;
        signal?.addEventListener('abort', () => {
          const err = new Error('Aborted');
          err.name = 'AbortError';
          reject(err);
        });
      });
    });

    fireEvent.click(screen.getAllByText('Run Simulation')[0]);

    const stopBtn = await waitFor(() => screen.getByText('Stop'));
    fireEvent.click(stopBtn);

    await waitFor(() => {
      expect(screen.queryByText('Stop')).not.toBeInTheDocument();
    });
    // silence unused-var lint on rejectFn assignment inside the executor
    expect(typeof rejectFn).toBe('function');
  });

  test('live sim reports a toast on a non-abort streaming error', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: /live sim/i }));

    const textarea = await waitFor(() => screen.getByPlaceholderText(/goal to simulate/i));
    fireEvent.change(textarea, { target: { value: 'Error goal' } });

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
      if (String(url).includes('simulation/stream')) {
        throw new Error('boom stream');
      }
      return new Response('{}', { status: 200 });
    });

    fireEvent.click(screen.getAllByText('Run Simulation')[0]);

    await waitFor(() => {
      expect(
        useToastStore.getState().toasts.some((t) => t.message.includes('Simulation error'))
      ).toBe(true);
    });
  });

  test('live sim processes step_completed events and shows session cost', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: /live sim/i }));

    const textarea = await waitFor(() => screen.getByPlaceholderText(/goal to simulate/i));
    fireEvent.change(textarea, { target: { value: 'Complete steps' } });

    const encoder = new TextEncoder();
    const payload =
      'data: {"type":"step_started","description":"Search Jira"}\n\n' +
      'data: {"type":"step_completed","cost_increment":0.02,"tool_called":"jira_search","output":"found 3 issues"}\n\n' +
      'not-data-prefixed line\n\n' +
      'data: {malformed json\n\n';
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(payload));
        controller.close();
      },
    });

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
      if (String(url).includes('simulation/stream')) {
        return new Response(stream, { status: 200 });
      }
      return new Response('{}', { status: 200 });
    });

    fireEvent.click(screen.getAllByText('Run Simulation')[0]);

    await waitFor(() => {
      expect(screen.getByText('Search Jira')).toBeDefined();
      expect(screen.getByText('Tool: jira_search')).toBeDefined();
      expect(screen.getByText(/found 3 issues/)).toBeDefined();
      expect(screen.getByText(/Estimated cost so far/)).toBeDefined();
    });
  });

  // ── Prompt Lab: extra branches ─────────────────────────────────────────────

  test('prompt lab: switching prompt key, cancel create, and empty state', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('/intelligence/prompt-variants')) {
        return new Response(JSON.stringify([]), { status: 200 });
      }
      return new Response('{}', { status: 200 });
    });

    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: /prompt lab/i }));

    await waitFor(() => {
      expect(screen.getByText(/no variants registered/i)).toBeDefined();
    });

    fireEvent.click(screen.getByText('executor'));
    fireEvent.click(screen.getByText('verifier'));

    fireEvent.click(screen.getByText('Add Challenger Variant'));
    await waitFor(() => screen.getByText('New Challenger Variant'));
    fireEvent.click(screen.getByText('Cancel'));

    await waitFor(() => {
      expect(screen.queryByText('New Challenger Variant')).not.toBeInTheDocument();
    });
  });

  test('prompt lab: loading skeleton shows while fetching variants', async () => {
    let resolveFetch: (r: Response) => void = () => {};
    vi.spyOn(globalThis, 'fetch').mockImplementation((url) => {
      const u = String(url);
      if (u.includes('/intelligence/prompt-variants')) {
        return new Promise((resolve) => {
          resolveFetch = resolve;
        });
      }
      return Promise.resolve(new Response('{}', { status: 200 }));
    });

    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: /prompt lab/i }));

    await waitFor(() => {
      expect(document.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0);
    });

    resolveFetch(new Response(JSON.stringify([]), { status: 200 }));
    await waitFor(() => screen.getByText(/no variants registered/i));
  });

  test('prompt lab: create variant shows error toast on failure', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: /prompt lab/i }));

    await waitFor(() => screen.getByText('Add Challenger Variant'));
    fireEvent.click(screen.getByText('Add Challenger Variant'));

    const nameInput = await waitFor(() => screen.getByPlaceholderText(/variant name/i));
    fireEvent.change(nameInput, { target: { value: 'Bad Variant' } });
    const promptInput = screen.getByPlaceholderText(/prompt text for this variant/i);
    fireEvent.change(promptInput, { target: { value: 'Some prompt' } });

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, opts) => {
      const u = String(url);
      const method = (opts as RequestInit)?.method ?? 'GET';
      if (u.includes('/intelligence/prompt-variants') && method === 'POST') {
        throw new Error('create failed');
      }
      return new Response(JSON.stringify([]), { status: 200 });
    });

    fireEvent.click(screen.getByText('Create'));

    await waitFor(() => {
      expect(
        useToastStore.getState().toasts.some((t) => t.message.includes('Create failed'))
      ).toBe(true);
    });
  });

  test('prompt lab: expanding a challenger card shows prompt text, and delete calls DELETE endpoint', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, opts) => {
      const u = String(url);
      const method = (opts as RequestInit)?.method ?? 'GET';
      if (u.includes('/intelligence/prompt-variants') && method === 'DELETE') {
        return new Response(null, { status: 204 });
      }
      if (u.includes('/intelligence/prompt-variants') && method === 'GET') {
        return new Response(
          JSON.stringify([
            {
              id: 'v1',
              key: 'planner',
              name: 'Control',
              prompt_text: 'Control prompt text',
              is_control: true,
              run_count: 5,
              mean_score: null,
              p95_score: null,
              promoted_at: null,
            },
            {
              id: 'v2',
              key: 'planner',
              name: 'Challenger One',
              prompt_text: 'Challenger prompt text',
              is_control: false,
              run_count: 1,
              mean_score: 0.5,
              p95_score: 0.6,
              promoted_at: null,
            },
          ]),
          { status: 200 }
        );
      }
      return new Response('{}', { status: 200 });
    });

    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: /prompt lab/i }));

    const challengerCard = await waitFor(() => {
      const card = screen.getByText('Challenger One').closest('.bg-card');
      expect(card).not.toBeNull();
      return card as HTMLElement;
    });
    expect(within(challengerCard).queryByText('Challenger prompt text')).not.toBeInTheDocument();

    const chevronBtn = within(challengerCard).getAllByRole('button')[0];
    fireEvent.click(chevronBtn);

    await waitFor(() => {
      expect(screen.getByText('Challenger prompt text')).toBeDefined();
    });

    const deleteBtn = screen.getByLabelText('Delete Challenger One');
    const fetchSpy = globalThis.fetch as ReturnType<typeof vi.fn>;
    fireEvent.click(deleteBtn);

    await waitFor(() => {
      const deleteCalls = fetchSpy.mock.calls.filter(
        (c) => (c[1] as RequestInit)?.method === 'DELETE'
      );
      expect(deleteCalls.length).toBeGreaterThan(0);
    });
  });

  test('prompt lab: promote and delete show error toasts on failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, opts) => {
      const u = String(url);
      const method = (opts as RequestInit)?.method ?? 'GET';
      if (u.includes('/promote')) {
        throw new Error('promote failed');
      }
      if (u.includes('/intelligence/prompt-variants') && method === 'DELETE') {
        throw new Error('delete failed');
      }
      if (u.includes('/intelligence/prompt-variants') && method === 'GET') {
        return new Response(
          JSON.stringify([
            {
              id: 'v1',
              key: 'planner',
              name: 'Control',
              prompt_text: 'Control',
              is_control: true,
              run_count: 1,
              mean_score: null,
              p95_score: null,
              promoted_at: null,
            },
            {
              id: 'v2',
              key: 'planner',
              name: 'Challenger',
              prompt_text: 'Challenger',
              is_control: false,
              run_count: 1,
              mean_score: null,
              p95_score: null,
              promoted_at: null,
            },
          ]),
          { status: 200 }
        );
      }
      return new Response('{}', { status: 200 });
    });

    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: /prompt lab/i }));

    const promoteButton = await waitFor(() => screen.getByText('Promote'));
    fireEvent.click(promoteButton);
    await waitFor(() => {
      expect(
        useToastStore.getState().toasts.some((t) => t.message.includes('Promote failed'))
      ).toBe(true);
    });

    const deleteButton = screen.getByLabelText('Delete Challenger');
    fireEvent.click(deleteButton);
    await waitFor(() => {
      expect(
        useToastStore.getState().toasts.some((t) => t.message.includes('Delete failed'))
      ).toBe(true);
    });
  });

  // ── Score tab: extra branches ───────────────────────────────────────────────

  test('score tab: selecting an agent from the dropdown updates the value', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: /score/i }));

    const agentSelect = await waitFor(() => screen.getByDisplayValue('— All agents —'));
    await waitFor(() => within(agentSelect).getByText('Agent Alpha'));
    fireEvent.change(agentSelect, { target: { value: 'a1' } });
    expect((agentSelect as HTMLSelectElement).value).toBe('a1');
  });

  test('score tab: shows empty state when there are no eval results', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('/intelligence/eval-suites') && !u.includes('/results')) {
        return new Response(JSON.stringify([]), { status: 200 });
      }
      return new Response(JSON.stringify([]), { status: 200 });
    });

    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: /score/i }));

    await waitFor(() => {
      expect(screen.getByText('No eval results')).toBeDefined();
      expect(screen.getByText(/— No suites —/)).toBeDefined();
    });
  });

  test('score tab: red team run shows error toast on failure', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: /score/i }));

    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
      throw new Error('red team down');
    });

    const rtButton = await waitFor(() => {
      const btns = screen.getAllByText(/Run Red Team/i);
      return btns.find((el) => el.tagName === 'BUTTON') ?? btns[0];
    });
    fireEvent.click(rtButton);

    await waitFor(() => {
      expect(
        useToastStore.getState().toasts.some((t) => t.message.includes('Red-team failed'))
      ).toBe(true);
    });
  });

  test('score tab: low security score renders in red/amber tier', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes('/red-team')) {
        return new Response(
          JSON.stringify({
            total: 10,
            passed: 5,
            failed: 5,
            results: [
              { case: 'prompt_injection', passed: false },
              { case: 'jailbreak_attempt', passed: true },
            ],
          }),
          { status: 201 }
        );
      }
      return new Response(JSON.stringify([]), { status: 200 });
    });

    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: /score/i }));

    const rtButton = await waitFor(() => {
      const btns = screen.getAllByText(/Run Red Team/i);
      return btns.find((el) => el.tagName === 'BUTTON') ?? btns[0];
    });
    fireEvent.click(rtButton);

    await waitFor(() => {
      expect(screen.getByText('50%')).toBeDefined();
      expect(screen.getByText('LEAKED')).toBeDefined();
    });
  });
});
