import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AgentDetailPage } from './AgentDetailPage';

// Companion suite to AgentDetailPage.test.tsx — targets UNTESTED branches:
// overview stats + recent goals, edit→save (PUT), clone (POST), readiness (GET),
// dry-run test (POST /goals), Versions tab rollback (POST), Permissions tab,
// Knowledge tab assign/remove, and the Rollout Gate tab.

const AGENT = {
  agent_id: 'agent-001',
  name: 'Code Reviewer',
  autonomy_mode: 'supervised',
  goal_template: 'Review all open PRs',
  status: 'active',
  created_at: '2025-01-01T00:00:00Z',
  default_model: 'gpt-4o',
  connector_ids: ['github', 'jira'],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function mockFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/readiness'))
      return json({ ready: true, score: 0.9, checks: [{ status: 'pass', message: 'Has connectors' }] });
    if (url.includes('/rollout-gate'))
      return json({ gate_passed: true, pass_rate: 0.9, run_count: 12, avg_score: 0.83, reason: 'Enough successful runs' });
    if (url.includes('/permissions'))
      return json({ agent_id: 'agent-001', permissions: [{ tool_name: 'web_search', level: 'allow' }] });
    if (url.includes('/versions'))
      return json([{ snapshot_id: 'snap-1', created_at: '2025-02-01T00:00:00Z', label: 'v1' }]);
    if (url.includes('/clone') && method === 'POST') return json(AGENT);
    if (/\/rollback\//.test(url) && method === 'POST') return json(AGENT);
    if (url.includes('/credentials')) return json([]);
    if (url.includes('/knowledge/collections'))
      return json([{ collection_id: 'kb-1', name: 'Docs', description: '', document_count: 3, created_at: '2025-01-01' }]);
    if (/\/agents\/[^/]+\/knowledge\//.test(url)) return json({ ok: true });
    if (url.includes('/connectors')) return json([]);
    if (url.includes('/goals') && method === 'POST') return json({ goal_id: 'goal-777', status: 'pending' });
    if (url.includes('/goals'))
      return json({ goals: [
        { goal_id: 'g-1', goal: 'Review PR #42', status: 'complete', agent_id: 'agent-001' },
        { goal_id: 'g-2', goal: 'Other agent goal', status: 'failed', agent_id: 'other' },
      ] });
    if (url.includes('/agents/agent-001') && method === 'PUT') return json({ ...AGENT, name: 'Renamed Agent' });
    return json(AGENT);
  });
}

function renderPage(agentId = 'agent-001') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={[`/agents/${agentId}`]}>
      <QueryClientProvider client={qc}>
        <Routes>
          <Route path="/agents/:agentId" element={<AgentDetailPage />} />
          <Route path="/agents" element={<div>Agents list</div>} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 'tenant-1', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('AgentDetailPage branches', () => {
  test('overview shows status stats and recent goals for this agent', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByTestId('agent-name')).toHaveTextContent('Code Reviewer');
    expect(screen.getByText('Default Model')).toBeInTheDocument();
    expect(screen.getByText('gpt-4o')).toBeInTheDocument();
    // Recent goals is filtered to this agent — the "other" agent's goal is excluded.
    expect(await screen.findByText('Review PR #42')).toBeInTheDocument();
    expect(screen.queryByText('Other agent goal')).not.toBeInTheDocument();
  });

  test('edit → save sends a PUT to the agent endpoint', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const nameInput = await screen.findByDisplayValue('Code Reviewer');
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, 'Renamed Agent');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/agents/agent-001') && (i as RequestInit)?.method === 'PUT',
      )).toBe(true),
    );
  });

  test('clone button POSTs to the clone endpoint', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('button', { name: /clone agent/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/agents/agent-001/clone') && (i as RequestInit)?.method === 'POST',
      )).toBe(true),
    );
  });

  test('check readiness calls the readiness endpoint and shows the widget', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('button', { name: /Check Readiness/i }));
    expect(await screen.findByText(/Production Ready/i)).toBeInTheDocument();
    expect(screen.getByText('Has connectors')).toBeInTheDocument();
    expect(spy.mock.calls.some(([u]) => String(u).includes('/agents/agent-001/readiness'))).toBe(true);
  });

  test('dry-run test submits a goal with dry_run and shows the result', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.type(screen.getByPlaceholderText(/Enter a test goal/i), 'Summarize the repo');
    await userEvent.click(screen.getByRole('button', { name: /Test \(Dry Run\)/i }));
    await waitFor(() => {
      const post = spy.mock.calls.find(([u, i]) =>
        String(u).includes('/goals') && (i as RequestInit)?.method === 'POST');
      expect(post).toBeTruthy();
      const body = JSON.parse(String((post?.[1] as RequestInit)?.body ?? '{}'));
      expect(body.dry_run).toBe(true);
      expect(body.agent_id).toBe('agent-001');
    });
    expect(await screen.findByText(/Goal submitted: goal-777/i)).toBeInTheDocument();
  });

  test('versions tab lists snapshots and rollback POSTs to the rollback endpoint', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('tab', { name: /versions/i }));
    await userEvent.click(await screen.findByRole('button', { name: /Version History/i }));
    expect(await screen.findByText('snap-1')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Rollback/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/agents/agent-001/rollback/snap-1') && (i as RequestInit)?.method === 'POST',
      )).toBe(true),
    );
  });

  test('permissions tab renders the tool permission rows', async () => {
    mockFetch();
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('tab', { name: /permissions/i }));
    expect(await screen.findByText('web_search')).toBeInTheDocument();
    expect(screen.getByText('allow')).toBeInTheDocument();
  });

  test('knowledge tab assign then remove hit the right endpoints', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('tab', { name: /knowledge/i }));
    const row = (await screen.findByText('Docs')).closest('div') as HTMLElement;
    await userEvent.click(within(row).getByRole('button', { name: 'Assign' }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/agents/agent-001/knowledge/kb-1') && (i as RequestInit)?.method === 'POST',
      )).toBe(true),
    );
    await userEvent.click(within(row).getByRole('button', { name: 'Remove' }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/agents/agent-001/knowledge/kb-1') && (i as RequestInit)?.method === 'DELETE',
      )).toBe(true),
    );
  });

  test('rollout gate tab renders the gate status and metrics', async () => {
    mockFetch();
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('tab', { name: /rollout gate/i }));
    expect(await screen.findByText(/Gate passed/i)).toBeInTheDocument();
    expect(screen.getByText('Pass rate')).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByText(/Enough successful runs/i)).toBeInTheDocument();
  });
});
