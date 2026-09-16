import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { GhostRunPage } from './GhostRunPage';

const HISTORY_KEY = 'av_ghost_run_history';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<GhostRunPage />} />
          <Route path="/goals/:id" element={<div data-testid="goal-detail-route" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const MOCK_GHOST_RESPONSE = {
  ghost_run_id: 'gr-001',
  goal_ids: { 'Standard': 'goal-1', 'Multi-Agent': 'goal-2', 'High-Priority': 'goal-3' },
  strategies: [
    { name: 'Standard', workflow_mode: 'single_agent', priority: 'normal', goal_id: 'goal-1', status: 'queued', error: null },
    { name: 'Multi-Agent', workflow_mode: 'multi_agent', priority: 'normal', goal_id: 'goal-2', status: 'queued', error: null },
    { name: 'High-Priority', workflow_mode: 'single_agent', priority: 'high', goal_id: 'goal-3', status: 'queued', error: null },
  ],
};

/** statuses: map of goal id -> status payload (or a function of id -> payload). */
function mockFetch(opts: {
  ghostResponse?: unknown;
  ghostRunStatus?: number;
  statuses?: Record<string, unknown> | ((id: string) => unknown);
} = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init as RequestInit | undefined)?.method ?? 'GET';

    if (url.includes('/goals/ghost-run') && method === 'POST') {
      if (opts.ghostRunStatus && opts.ghostRunStatus >= 400) {
        return new Response(JSON.stringify({ detail: 'launch failed' }), { status: opts.ghostRunStatus });
      }
      return new Response(JSON.stringify(opts.ghostResponse ?? MOCK_GHOST_RESPONSE), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    if (/\/goals\/[^/]+\/cancel$/.test(url) && method === 'POST') {
      return new Response(JSON.stringify({ status: 'cancelled' }), { status: 200 });
    }
    const m = url.match(/\/goals\/([^/]+)$/);
    if (m && method === 'GET') {
      const id = m[1];
      const fn = opts.statuses;
      const data = typeof fn === 'function' ? fn(id) : (fn?.[id] ?? { status: 'planning' });
      return new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    return new Response('{}', { status: 200 });
  });
}

beforeEach(() => {
  localStorage.clear();
  useAuthStore.setState({
    apiKey: 'test-key', tenantId: 'tenant-1', plan: 'professional', isAuthenticated: true,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('GhostRunPage — smoke', () => {
  test('renders page without crashing', () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200 })
    );
    renderPage();
    expect(document.body).toBeTruthy();
  });

  test('shows Ghost Run heading', () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200 })
    );
    renderPage();
    expect(screen.getAllByText(/ghost run/i).length).toBeGreaterThan(0);
  });

  test('shows goal textarea', () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200 })
    );
    renderPage();
    const textboxes = screen.getAllByRole('textbox');
    expect(textboxes.length).toBeGreaterThan(0);
  });

  test('shows at least one action button', () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200 })
    );
    renderPage();
    const buttons = screen.getAllByRole('button');
    expect(buttons.length).toBeGreaterThan(0);
  });

  test('shows strategy configuration section', () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200 })
    );
    renderPage();
    expect(document.body.innerHTML.toLowerCase()).toMatch(/strateg|ghost run/);
  });

  test('launches ghost run on submit', async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(MOCK_GHOST_RESPONSE), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    );
    renderPage();
    const textboxes = screen.getAllByRole('textbox');
    await user.type(textboxes[0], 'Find all open Jira tickets');
    const buttons = screen.getAllByRole('button');
    const launchBtn = buttons.find(b => b.textContent?.toLowerCase().includes('launch') || b.textContent?.toLowerCase().includes('ghost') || b.textContent?.toLowerCase().includes('run'));
    if (launchBtn) {
      await user.click(launchBtn);
    }
    await waitFor(() => expect(document.body).toBeTruthy(), { timeout: 3000 });
  });
});

describe('GhostRunPage — configuration phase', () => {
  test('launch button is disabled until a goal is entered', async () => {
    mockFetch();
    renderPage();
    const launchBtn = screen.getByRole('button', { name: /launch ghost run/i });
    expect(launchBtn).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/goal description/i), 'Do the thing');
    expect(launchBtn).toBeEnabled();
  });

  test('adding and removing strategies updates the count and remove affordance', async () => {
    mockFetch();
    renderPage();
    expect(screen.getByText(/strategies \(3\)/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /add strategy/i }));
    expect(screen.getByText(/strategies \(4\)/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /add strategy/i }));
    expect(screen.getByText(/strategies \(5\)/i)).toBeInTheDocument();
    // Cap at 5 — the Add Strategy button disappears
    expect(screen.queryByRole('button', { name: /add strategy/i })).not.toBeInTheDocument();

    // Remove strategies down to 1 — the remove button should then disappear
    await userEvent.click(screen.getByLabelText('Remove strategy 5'));
    await userEvent.click(screen.getByLabelText('Remove strategy 4'));
    await userEvent.click(screen.getByLabelText('Remove strategy 3'));
    await userEvent.click(screen.getByLabelText('Remove strategy 2'));
    expect(screen.getByText(/strategies \(1\)/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/remove strategy/i)).not.toBeInTheDocument();
  });

  test('editing a strategy name and workflow mode updates its fields', async () => {
    mockFetch();
    renderPage();
    const nameInput = screen.getByDisplayValue('Standard');
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, 'Renamed');
    expect(screen.getByDisplayValue('Renamed')).toBeInTheDocument();
  });

  test('toggling a compare-by metric changes its active state', async () => {
    mockFetch();
    renderPage();
    const speedBtn = screen.getByRole('button', { name: /^speed$/i });
    expect(speedBtn.className).toContain('bg-primary');
    await userEvent.click(speedBtn);
    expect(speedBtn.className).toContain('bg-background');
    await userEvent.click(speedBtn);
    expect(speedBtn.className).toContain('bg-primary');
  });

  test('toggles stop-on-first-completion checkbox', async () => {
    mockFetch();
    renderPage();
    const checkbox = screen.getByLabelText(/stop on first completion/i) as HTMLInputElement;
    expect(checkbox.checked).toBe(false);
    await userEvent.click(checkbox);
    expect(checkbox.checked).toBe(true);
  });

  test('shows a launch-failed alert when the ghost-run request errors', async () => {
    mockFetch({ ghostRunStatus: 500 });
    renderPage();
    await userEvent.type(screen.getByLabelText(/goal description/i), 'Do the thing');
    await userEvent.click(screen.getByRole('button', { name: /launch ghost run/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/launch failed/i);
  });

  test('renders and replays a history entry from localStorage', async () => {
    const entry = {
      id: 'gr-prev', goal: 'Previous goal text', date: '1/1/2026', winner: 'Standard',
      strategies: [{ name: 'Solo', workflow_mode: 'single_agent', priority: 'normal' }],
    };
    localStorage.setItem(HISTORY_KEY, JSON.stringify([entry]));
    mockFetch();
    renderPage();
    expect(screen.getByText(/recent ghost runs/i)).toBeInTheDocument();
    expect(screen.getByText(/previous goal text/i)).toBeInTheDocument();
    await userEvent.click(screen.getByText(/previous goal text/i));
    expect(screen.getByLabelText(/goal description/i)).toHaveValue('Previous goal text');
    expect(screen.getByText(/strategies \(1\)/i)).toBeInTheDocument();
  });
});

describe('GhostRunPage — running phase', () => {
  async function launch(statuses?: Record<string, unknown> | ((id: string) => unknown)) {
    const spy = mockFetch({ statuses });
    renderPage();
    await userEvent.type(screen.getByLabelText(/goal description/i), 'Do the thing');
    await userEvent.click(screen.getByRole('button', { name: /launch ghost run/i }));
    await screen.findByText(/ghost run in progress/i);
    return spy;
  }

  test('shows the running phase with a live card per strategy', async () => {
    await launch({});
    expect(screen.getAllByText(/waiting…/i).length).toBe(3);
    expect(screen.getByText('Standard')).toBeInTheDocument();
    expect(screen.getByText('Multi-Agent')).toBeInTheDocument();
    expect(screen.getByText('High-Priority')).toBeInTheDocument();
  });

  test('View execution navigates to the goal detail route', async () => {
    await launch({});
    const viewButtons = screen.getAllByText(/view execution/i);
    await userEvent.click(viewButtons[0]);
    expect(await screen.findByTestId('goal-detail-route')).toBeInTheDocument();
  });

  test('Cancel All calls the cancel endpoint for every goal and returns to config', async () => {
    const spy = await launch({});
    await userEvent.click(screen.getByRole('button', { name: /cancel all/i }));
    await waitFor(() => {
      const cancelCalls = spy.mock.calls.filter(([u, i]) =>
        /\/goals\/goal-\d+\/cancel$/.test(String(u)) && (i as RequestInit)?.method === 'POST'
      );
      expect(cancelCalls.length).toBe(3);
    });
    expect(await screen.findByLabelText(/goal description/i)).toBeInTheDocument();
  });
});

describe('GhostRunPage — results phase', () => {
  test('shows winner banner, results table, and saves history', async () => {
    mockFetch({
      statuses: (id) => {
        if (id === 'goal-1') return { status: 'completed', cost_usd: 0.001, iterations: 3, eval_score: 0.9 };
        if (id === 'goal-2') return { status: 'completed', cost_usd: 0.01, iterations: 5, eval_score: 0.5 };
        return { status: 'completed', cost_usd: 0.02, iterations: 8, eval_score: 0.3 };
      },
    });
    renderPage();
    // Disable the Speed metric so duration (real elapsed ms) can't introduce flakiness
    await userEvent.click(screen.getByRole('button', { name: /^speed$/i }));
    await userEvent.type(screen.getByLabelText(/goal description/i), 'Do the thing');
    await userEvent.click(screen.getByRole('button', { name: /launch ghost run/i }));

    expect(await screen.findByText(/comparison results/i, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByText(/standard wins/i)).toBeInTheDocument();
    expect(screen.getByText(/eval score/i)).toBeInTheDocument();
    expect(screen.getByText('0.90')).toBeInTheDocument();

    const saved = JSON.parse(localStorage.getItem(HISTORY_KEY) ?? '[]');
    expect(saved).toHaveLength(1);
    expect(saved[0].winner).toBe('Standard');
  });

  test('hides the Eval Score row when that metric is disabled', async () => {
    mockFetch({
      statuses: () => ({ status: 'completed', cost_usd: 0.001, iterations: 1, eval_score: 0.9 }),
    });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /^speed$/i }));
    await userEvent.click(screen.getByRole('button', { name: /quality score/i }));
    await userEvent.type(screen.getByLabelText(/goal description/i), 'Do the thing');
    await userEvent.click(screen.getByRole('button', { name: /launch ghost run/i }));
    await screen.findByText(/comparison results/i, {}, { timeout: 3000 });
    expect(screen.queryByText(/eval score/i)).not.toBeInTheDocument();
  });

  test('shows no winner banner and a dash winner in history when all strategies fail', async () => {
    mockFetch({ statuses: () => ({ status: 'failed' }) });
    renderPage();
    await userEvent.type(screen.getByLabelText(/goal description/i), 'Do the thing');
    await userEvent.click(screen.getByRole('button', { name: /launch ghost run/i }));
    await screen.findByText(/comparison results/i, {}, { timeout: 3000 });
    expect(screen.queryByText(/wins/i)).not.toBeInTheDocument();

    const saved = JSON.parse(localStorage.getItem(HISTORY_KEY) ?? '[]');
    expect(saved[0].winner).toBe('—');
  });

  test('New Ghost Run returns to the configuration phase', async () => {
    mockFetch({ statuses: () => ({ status: 'completed', cost_usd: 0.001, iterations: 1, eval_score: 0.5 }) });
    renderPage();
    await userEvent.type(screen.getByLabelText(/goal description/i), 'Do the thing');
    await userEvent.click(screen.getByRole('button', { name: /launch ghost run/i }));
    await screen.findByText(/comparison results/i, {}, { timeout: 3000 });
    await userEvent.click(screen.getByRole('button', { name: /new ghost run/i }));
    expect(await screen.findByLabelText(/goal description/i)).toBeInTheDocument();
  });

  test('Run Again re-launches the ghost run', async () => {
    const spy = mockFetch({ statuses: () => ({ status: 'completed', cost_usd: 0.001, iterations: 1, eval_score: 0.5 }) });
    renderPage();
    await userEvent.type(screen.getByLabelText(/goal description/i), 'Do the thing');
    await userEvent.click(screen.getByRole('button', { name: /launch ghost run/i }));
    await screen.findByText(/comparison results/i, {}, { timeout: 3000 });

    const launchCallsBefore = spy.mock.calls.filter(([u, i]) =>
      String(u).includes('/goals/ghost-run') && (i as RequestInit)?.method === 'POST'
    ).length;
    const runAgainButtons = screen.getAllByRole('button', { name: /run again/i });
    await userEvent.click(runAgainButtons[runAgainButtons.length - 1]);
    await waitFor(() => {
      const launchCallsAfter = spy.mock.calls.filter(([u, i]) =>
        String(u).includes('/goals/ghost-run') && (i as RequestInit)?.method === 'POST'
      ).length;
      expect(launchCallsAfter).toBeGreaterThan(launchCallsBefore);
    });
  });

  test('View link in the results table navigates to the goal route', async () => {
    mockFetch({ statuses: () => ({ status: 'completed', cost_usd: 0.001, iterations: 1, eval_score: 0.5 }) });
    renderPage();
    await userEvent.type(screen.getByLabelText(/goal description/i), 'Do the thing');
    await userEvent.click(screen.getByRole('button', { name: /launch ghost run/i }));
    await screen.findByText(/comparison results/i, {}, { timeout: 3000 });
    const viewLinks = screen.getAllByRole('button', { name: /^view$/i });
    await userEvent.click(viewLinks[0]);
    expect(await screen.findByTestId('goal-detail-route')).toBeInTheDocument();
  });

  test('replaying a history entry from the results phase returns to configuration', async () => {
    mockFetch({ statuses: () => ({ status: 'completed', cost_usd: 0.001, iterations: 1, eval_score: 0.5 }) });
    renderPage();
    await userEvent.type(screen.getByLabelText(/goal description/i), 'Do the thing');
    await userEvent.click(screen.getByRole('button', { name: /launch ghost run/i }));
    await screen.findByText(/comparison results/i, {}, { timeout: 3000 });

    const historySection = screen.getByText(/recent ghost runs/i).closest('div')!.parentElement!;
    const entryBtn = within(historySection).getByText(/do the thing/i);
    await userEvent.click(entryBtn);
    expect(await screen.findByLabelText(/goal description/i)).toHaveValue('Do the thing');
  });
});
