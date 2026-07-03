import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AgentLabPage } from './AgentLabPage';

// ── Mocks ─────────────────────────────────────────────────────────────────────

vi.mock('@/stores/toast', () => ({ toast: vi.fn() }));

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
});
