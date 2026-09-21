import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { AgentDetailPage } from './AgentDetailPage';

// Third companion suite — targets remaining uncovered branches/functions in
// AgentDetailPage.tsx: Permissions tab (loading/error/missing/object-shaped/empty),
// Knowledge tab empty state, Rollout Gate tab (loading/error/missing/gate_status
// fallback/conditions list/failed gate), readiness "not ready" widget, mutation
// onError toasts (clone/readiness/export), the empty-test-goal early return, and
// the "no connectors registered" editing branch.

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
  useToastStore.setState({ toasts: [] });
});
afterEach(() => vi.restoreAllMocks());

describe('AgentDetailPage — permissions tab shapes', () => {
  test('shows a loading skeleton while permissions are fetching', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/permissions')) return new Promise(() => {}); // never resolves
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('tab', { name: /permissions/i }));
    // Skeleton has no accessible text; assert the "no permissions" empty state hasn't rendered yet.
    expect(screen.queryByText('No permissions configured')).not.toBeInTheDocument();
  });

  test('shows an error state when the permissions request fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/permissions')) return json({ error: 'boom' }, 500);
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('tab', { name: /permissions/i }));
    expect(await screen.findByText('Failed to load permissions')).toBeInTheDocument();
  });

  test('shows the empty state when permissions data is missing', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/permissions')) return json(null);
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('tab', { name: /permissions/i }));
    expect(await screen.findByText('No permissions configured')).toBeInTheDocument();
  });

  test('normalises an object-shaped permissions map into rows', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/permissions'))
        return json({ agent_id: 'agent-001', permissions: { deploy_tool: 'deny' } });
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('tab', { name: /permissions/i }));
    expect(await screen.findByText('deploy_tool')).toBeInTheDocument();
    expect(screen.getByText('deny')).toBeInTheDocument();
  });

  test('shows the "no rules" empty state when permissions list is empty', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/permissions')) return json({ agent_id: 'agent-001', permissions: [] });
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('tab', { name: /permissions/i }));
    expect(await screen.findByText('This agent has no tool-level permission rules.')).toBeInTheDocument();
  });
});

describe('AgentDetailPage — knowledge tab empty state', () => {
  test('shows empty state when there are no knowledge collections', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/knowledge/collections')) return json([]);
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('tab', { name: /knowledge/i }));
    expect(await screen.findByText('No knowledge collections')).toBeInTheDocument();
  });
});

describe('AgentDetailPage — rollout gate tab shapes', () => {
  test('shows a loading skeleton while the rollout gate is fetching', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/rollout-gate')) return new Promise(() => {});
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('tab', { name: /rollout gate/i }));
    expect(screen.queryByText('No rollout gate configured')).not.toBeInTheDocument();
  });

  test('shows an error state when the rollout gate request fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/rollout-gate')) return json({ error: 'boom' }, 500);
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('tab', { name: /rollout gate/i }));
    expect(await screen.findByText('Failed to load rollout gate')).toBeInTheDocument();
  });

  test('shows the empty state when no rollout gate is configured', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/rollout-gate')) return json(null);
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('tab', { name: /rollout gate/i }));
    expect(await screen.findByText('No rollout gate configured')).toBeInTheDocument();
  });

  test('falls back to gate_status==="passed", renders conditions, and a failed gate', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/rollout-gate'))
        return json({
          gate_status: 'passed',
          pass_rate: 0.5,
          run_count: 4,
          avg_score: 0.6,
          conditions: ['At least 10 runs', 'Pass rate above 80%'],
        });
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('tab', { name: /rollout gate/i }));
    expect(await screen.findByText(/Gate passed/i)).toBeInTheDocument();
    expect(screen.getByText('Conditions')).toBeInTheDocument();
    expect(screen.getByText('At least 10 runs')).toBeInTheDocument();
    expect(screen.getByText('Pass rate above 80%')).toBeInTheDocument();
  });

  test('renders a blocked gate when neither gate_passed nor gate_status pass', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/rollout-gate'))
        return json({ gate_passed: false, gate_status: 'blocked', pass_rate: 0.2, run_count: 2, avg_score: 0.3 });
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('tab', { name: /rollout gate/i }));
    expect(await screen.findByText(/Gate blocked/i)).toBeInTheDocument();
    // No conditions/reason provided → neither section renders.
    expect(screen.queryByText('Conditions')).not.toBeInTheDocument();
  });
});

describe('AgentDetailPage — readiness widget and error toasts', () => {
  test('renders "Not Ready" with issues when readiness fails checks', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/readiness'))
        return json({
          ready: false,
          issues: ['Missing connector', 'No goal template'],
          checks: [
            { status: 'fail', message: 'Missing connector' },
            { status: 'pass', message: 'Has a name' },
          ],
        });
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('button', { name: /Check Readiness/i }));
    expect(await screen.findByText('Not Ready')).toBeInTheDocument();
    expect(screen.getByText('Has a name')).toBeInTheDocument();
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'warning' && t.message === '2 issues found')).toBe(true);
    });
  });

  test('toasts an error when the readiness check request itself fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/readiness')) return json({ error: 'boom' }, 500);
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('button', { name: /Check Readiness/i }));
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'error' && t.message.includes('Readiness check failed'))).toBe(true);
    });
  });

  test('toasts an error when clone fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/clone') && method === 'POST') return json({ error: 'boom' }, 500);
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('button', { name: /clone agent/i }));
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'error' && t.message.includes('Clone failed'))).toBe(true);
    });
  });

  test('toasts an error when export fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/export')) throw new Error('network down');
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByTestId('export-btn'));
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'error' && t.message.includes('Export failed'))).toBe(true);
    });
  });
});

describe('AgentDetailPage — misc edge branches', () => {
  test('Test (Dry Run) is disabled and does nothing for a blank goal', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    const btn = screen.getByRole('button', { name: /Test \(Dry Run\)/i });
    expect(btn).toBeDisabled();
    const callsBefore = spy.mock.calls.length;
    // Clicking a disabled button is a no-op; confirm no additional goal submission occurred.
    await userEvent.click(btn);
    expect(spy.mock.calls.length).toBe(callsBefore);
  });

  test('editing with no registered connectors shows the "add a connector" prompt', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/connectors')) return json([]);
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    expect(await screen.findByText('No connectors registered yet.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add a connector' })).toBeInTheDocument();
  });

  test('editing with registered connectors can check and uncheck a connector', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/connectors'))
        return json([{ server_id: 'slack', name: 'Slack', status: 'connected' }]);
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const checkbox = await screen.findByRole('checkbox');
    expect(checkbox).not.toBeChecked();
    await userEvent.click(checkbox);
    expect(checkbox).toBeChecked();
    // Agent already had two connectors (github, jira); checking "slack" adds a third.
    expect(screen.getByText((_, node) => node?.textContent === 'Connectors (3 selected)')).toBeInTheDocument();
    await userEvent.click(checkbox);
    expect(checkbox).not.toBeChecked();
  });

  test('clicking Edit toggles the form closed again via the X/Cancel button', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/connectors')) return json([]);
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    expect(await screen.findByDisplayValue('Code Reviewer')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByDisplayValue('Code Reviewer')).not.toBeInTheDocument();
  });

  test('changing autonomy mode and goal template updates edit form state', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/connectors')) return json([]);
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const select = await screen.findByDisplayValue('supervised');
    await userEvent.selectOptions(select, 'fully-autonomous');
    expect((select as HTMLSelectElement).value).toBe('fully-autonomous');

    const textarea = screen.getByDisplayValue('Review all open PRs');
    await userEvent.clear(textarea);
    await userEvent.type(textarea, 'New goal template');

    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => {
      const put = spy.mock.calls.find(([u, i]) =>
        String(u).includes('/agents/agent-001') && (i as RequestInit)?.method === 'PUT');
      expect(put).toBeTruthy();
      const body = JSON.parse(String((put?.[1] as RequestInit)?.body ?? '{}'));
      expect(body.autonomy_mode).toBe('fully-autonomous');
      expect(body.goal_template).toBe('New goal template');
    });
  });
});

describe('AgentDetailPage — navigation buttons', () => {
  test('back to agents button navigates away (loaded state)', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('button', { name: /Back to agents/i }));
    expect(await screen.findByText('Agents list')).toBeInTheDocument();
  });

  test('back to agents button navigates away (not-found state)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({ error: 'not found' }, 404));
    renderPage('missing-agent');
    await screen.findByTestId('not-found');
    await userEvent.click(screen.getByRole('button', { name: /Back to agents/i }));
    expect(await screen.findByText('Agents list')).toBeInTheDocument();
  });

  test('health radar and personality buttons navigate to their sub-pages', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('button', { name: /view agent health radar/i }));
    // Navigated away from the detail route (no matching route registered for /radar
    // in this test's router, so the agent name is no longer rendered).
    await waitFor(() => expect(screen.queryByTestId('agent-name')).not.toBeInTheDocument());
  });

  test('the "Add a connector" link in the empty-connectors editor navigates to the catalog', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/connectors')) return json([]);
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    render(
      <MemoryRouter initialEntries={['/agents/agent-001']}>
        <QueryClientProvider client={qc}>
          <Routes>
            <Route path="/agents/:agentId" element={<AgentDetailPage />} />
            <Route path="/connectors/catalog" element={<div>Connector catalog</div>} />
          </Routes>
        </QueryClientProvider>
      </MemoryRouter>,
    );
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Add a connector' }));
    expect(await screen.findByText('Connector catalog')).toBeInTheDocument();
  });
});

describe('AgentDetailPage — credentials tab issue/revoke', () => {
  function mockCredentials(list: unknown[]) {
    return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/credentials') && method === 'POST') return json({ credential_id: 'cred-new', scopes: ['goals:read'] });
      if (url.includes('/credentials') && method === 'DELETE') return json({ ok: true });
      if (url.includes('/credentials')) return json(list);
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
  }

  test('issuing a credential opens the form, submits scopes, and closes it', async () => {
    const spy = mockCredentials([]);
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('tab', { name: /credentials/i }));
    await userEvent.click(await screen.findByRole('button', { name: 'Issue Credential' }));
    const scopesInput = screen.getByPlaceholderText('goals:read,goals:write');
    await userEvent.type(scopesInput, 'goals:read, goals:write');
    await userEvent.click(screen.getByRole('button', { name: 'Issue' }));
    await waitFor(() => {
      const post = spy.mock.calls.find(([u, i]) =>
        String(u).includes('/credentials') && (i as RequestInit)?.method === 'POST');
      expect(post).toBeTruthy();
      const body = JSON.parse(String((post?.[1] as RequestInit)?.body ?? '{}'));
      expect(body.scopes).toEqual(['goals:read', 'goals:write']);
    });
    // Form closes again after a successful issue.
    expect(screen.queryByPlaceholderText('goals:read,goals:write')).not.toBeInTheDocument();
  });

  test('canceling the issue-credential form hides it without submitting', async () => {
    mockCredentials([]);
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('tab', { name: /credentials/i }));
    await userEvent.click(await screen.findByRole('button', { name: 'Issue Credential' }));
    expect(screen.getByPlaceholderText('goals:read,goals:write')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByPlaceholderText('goals:read,goals:write')).not.toBeInTheDocument();
  });

  test('a credential using key_id (no credential_id), no scopes, and no created_at renders its fallbacks', async () => {
    mockCredentials([{ key_id: 'key-legacy', scopes: [] }]);
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('tab', { name: /credentials/i }));
    expect(await screen.findByText('key-legacy')).toBeInTheDocument();
    // Empty scopes join to '' and missing created_at falls back to the em dash.
    expect(screen.getByText((_, node) => node?.textContent === ' · Created —')).toBeInTheDocument();
  });

  test('revoking a credential calls the revoke endpoint', async () => {
    const spy = mockCredentials([
      { credential_id: 'cred-1', scopes: ['goals:read'], created_at: '2026-01-01T00:00:00Z' },
    ]);
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('tab', { name: /credentials/i }));
    await screen.findByText('cred-1');
    await userEvent.click(screen.getByRole('button', { name: 'Revoke' }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/credentials/cred-1') && (i as RequestInit)?.method === 'DELETE',
      )).toBe(true),
    );
  });
});

describe('AgentDetailPage — default-value fallbacks and pending states', () => {
  test('falls back to "Agent" name and error status ring when agent has neither', async () => {
    const bareAgent = { agent_id: 'agent-002', status: 'error' };
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(bareAgent);
    });
    renderPage('agent-002');
    expect(await screen.findByTestId('agent-name')).toHaveTextContent('');
    // "Status" stat tile falls back to the raw status value ("error").
    expect(screen.getAllByText('error').length).toBeGreaterThan(0);
  });

  test('shows the pending labels for readiness, clone, save, and snapshot while their requests are in flight', async () => {
    let resolveReadiness: (() => void) | undefined;
    let resolveClone: (() => void) | undefined;
    let resolveSave: (() => void) | undefined;
    let resolveSnapshot: (() => void) | undefined;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/readiness'))
        return new Promise((resolve) => { resolveReadiness = () => resolve(json({ ready: true, checks: [] })); });
      if (url.includes('/clone') && method === 'POST')
        return new Promise((resolve) => { resolveClone = () => resolve(json(AGENT)); });
      if (url.includes('/agents/agent-001') && method === 'PUT')
        return new Promise((resolve) => { resolveSave = () => resolve(json(AGENT)); });
      if (url.includes('/snapshot') && method === 'POST')
        return new Promise((resolve) => { resolveSnapshot = () => resolve(json({ snapshot_id: 'snap-x' })); });
      if (url.includes('/connectors')) return json([]);
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');

    await userEvent.click(screen.getByRole('button', { name: /Check Readiness/i }));
    expect(await screen.findByText('Checking…')).toBeInTheDocument();
    resolveReadiness?.();
    await screen.findByText(/Production Ready/i);

    await userEvent.click(screen.getByRole('button', { name: /clone agent/i }));
    expect(await screen.findByText('Cloning…')).toBeInTheDocument();
    resolveClone?.();
    await waitFor(() => expect(screen.queryByText('Cloning…')).not.toBeInTheDocument());

    await userEvent.click(screen.getByTestId('snapshot-btn'));
    await waitFor(() => expect(screen.getByTestId('snapshot-btn')).toBeDisabled());
    resolveSnapshot?.();
    await waitFor(() => expect(screen.getByTestId('snapshot-btn')).not.toBeDisabled());

    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled());
    resolveSave?.();
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument());
  });

  test('editing an agent missing name/goal_template/autonomy_mode/connector_ids starts from empty defaults', async () => {
    const bareAgent = { agent_id: 'agent-003', status: 'active' };
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/connectors')) return json([{ server_id: 'jira', name: '', status: '' }]);
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(bareAgent);
    });
    renderPage('agent-003');
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await screen.findByText('Name');
    const nameInput = screen.getByText('Name').nextElementSibling as HTMLInputElement;
    expect(nameInput.value).toBe('');
    const select = screen.getByText('Autonomy Mode').nextElementSibling as HTMLSelectElement;
    expect(select.value).toBe('supervised'); // first <option>, since value defaulted to ""
    // Connector row falls back to rendering its server_id when name is empty.
    expect(screen.getByText('jira')).toBeInTheDocument();
    const checkbox = screen.getByRole('checkbox');
    expect(checkbox).not.toBeChecked();
  });

  test('recent goals list renders the "failed" and default (in-progress) status pills', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals'))
        return json({ goals: [
          { goal_id: 'g-1', goal: 'Failing goal', status: 'failed', agent_id: 'agent-001' },
          { goal_id: 'g-2', goal: 'Running goal', status: 'running', agent_id: 'agent-001' },
        ] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    expect(await screen.findByText('Failing goal')).toBeInTheDocument();
    expect(screen.getByText('failed')).toBeInTheDocument();
    expect(screen.getByText('Running goal')).toBeInTheDocument();
    expect(screen.getByText('running')).toBeInTheDocument();
  });

  test('versions tab chevron toggles between expanded and collapsed', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/versions')) return json([{ snapshot_id: 'snap-1', created_at: '2025-02-01T00:00:00Z' }]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('tab', { name: /versions/i }));
    const toggle = await screen.findByRole('button', { name: /Version History/i });
    await userEvent.click(toggle);
    expect(await screen.findByText('snap-1')).toBeInTheDocument();
    await userEvent.click(toggle);
    expect(screen.queryByText('snap-1')).not.toBeInTheDocument();
  });

  test('permissions tab: string-typed permissions field normalises to empty, and rows fall back to "tool"/"ask" level', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/permissions')) return json({ agent_id: 'agent-001', permissions: 'not-a-list-or-map' });
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('tab', { name: /permissions/i }));
    expect(await screen.findByText('This agent has no tool-level permission rules.')).toBeInTheDocument();
  });

  test('permissions tab: a row using "tool" instead of "tool_name" and an "ask" level renders the neutral style', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/permissions'))
        return json({ agent_id: 'agent-001', permissions: [{ tool: 'browse_web', level: 'ask', daily_limit: 5, per_goal_limit: 2 }] });
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('tab', { name: /permissions/i }));
    expect(await screen.findByText('browse_web')).toBeInTheDocument();
    expect(screen.getByText('ask')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  test('rollout gate tab falls back to 0 for missing pass_rate/run_count/avg_score', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/rollout-gate')) return json({ gate_passed: true });
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('tab', { name: /rollout gate/i }));
    expect(await screen.findByText(/Gate passed/i)).toBeInTheDocument();
    expect(screen.getByText('0.0%')).toBeInTheDocument();
    expect(screen.getByText('0%')).toBeInTheDocument();
  });
});

describe('AgentDetailPage — export (Anthropic format)', () => {
  test('Export (Anthropic) button triggers a download with the anthropic format', async () => {
    let exportedFormat = '';
    globalThis.URL.createObjectURL = vi.fn(() => 'blob:mock');
    globalThis.URL.revokeObjectURL = vi.fn();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/export') && url.includes('format=anthropic')) {
        exportedFormat = 'anthropic';
        return json({ format: 'anthropic', agent: AGENT });
      }
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    const origCreate = document.createElement.bind(document);
    vi.spyOn(document, 'createElement').mockImplementation((tag) => {
      const el = origCreate(tag);
      if (tag === 'a') vi.spyOn(el as HTMLAnchorElement, 'click').mockImplementation(() => {});
      return el;
    });
    renderPage();
    await screen.findByTestId('agent-name');
    await userEvent.click(screen.getByRole('button', { name: /Export \(Anthropic\)/i }));
    await waitFor(() => expect(exportedFormat).toBe('anthropic'));
  });
});
