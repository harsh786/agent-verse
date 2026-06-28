import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AgentDetailPage } from './AgentDetailPage';

const MOCK_AGENT = {
  agent_id: 'agent-001',
  name: 'Code Reviewer',
  autonomy_mode: 'supervised',
  goal_template: 'Review all open PRs',
  status: 'active',
  created_at: '2025-01-01T00:00:00Z',
  default_model: 'gpt-4o',
  connector_ids: ['github', 'jira'],
};

function renderPage(agentId = 'agent-001') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MemoryRouter initialEntries={[`/agents/${agentId}`]}>
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route path="/agents/:agentId" element={<AgentDetailPage />} />
          <Route path="/agents" element={<div>Agents list</div>} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

describe('AgentDetailPage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      apiKey: 'test-key',
      tenantId: 'tenant-1',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // Test 1: Shows agent name
  test('shows agent name after load', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => MOCK_AGENT,
    } as Response);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('agent-name')).toHaveTextContent('Code Reviewer');
    });
  });

  // Test 2: Shows loading state
  test('shows loading state initially', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getByTestId('loading')).toBeInTheDocument();
  });

  // Test 3: Shows error when agent not found
  test('shows error when agent not found', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      status: 404,
      statusText: 'Not Found',
      json: async () => ({ error: { message: 'Not found' } }),
    } as Response);

    renderPage('nonexistent-id');

    await waitFor(() => {
      expect(screen.getByTestId('not-found')).toBeInTheDocument();
    });
  });

  // Test 4: Shows connector list
  test('shows connector requirements', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => MOCK_AGENT,
    } as Response);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('connector-list')).toBeInTheDocument();
    });
    expect(screen.getByText('github')).toBeInTheDocument();
  });

  // Test 5: Snapshot button calls API
  test('snapshot button calls snapshot API', async () => {
    const user = userEvent.setup();
    let snapshotCalled = false;

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
      if (String(url).includes('/snapshot')) {
        snapshotCalled = true;
        return {
          ok: true,
          status: 200,
          json: async () => ({ snapshot_id: 'snap-123' }),
        } as Response;
      }
      // versions endpoint
      if (String(url).includes('/versions')) {
        return { ok: true, status: 200, json: async () => [] } as Response;
      }
      // goals
      if (String(url).includes('/goals')) {
        return { ok: true, status: 200, json: async () => ({ goals: [] }) } as Response;
      }
      return { ok: true, status: 200, json: async () => MOCK_AGENT } as Response;
    });

    renderPage();

    await waitFor(() => screen.getByTestId('snapshot-btn'));
    await user.click(screen.getByTestId('snapshot-btn'));

    await waitFor(() => {
      expect(snapshotCalled).toBe(true);
    });
  });

  // Test 6: Export button calls API
  test('export button initiates export', async () => {
    const user = userEvent.setup();
    let exportCalled = false;

    // Mock URL.createObjectURL
    globalThis.URL.createObjectURL = vi.fn(() => 'blob:mock');
    globalThis.URL.revokeObjectURL = vi.fn();

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
      if (String(url).includes('/export')) {
        exportCalled = true;
        return {
          ok: true,
          status: 200,
          json: async () => ({ format: 'openai', agent: MOCK_AGENT }),
        } as Response;
      }
      if (String(url).includes('/versions')) {
        return { ok: true, status: 200, json: async () => [] } as Response;
      }
      if (String(url).includes('/goals')) {
        return { ok: true, status: 200, json: async () => ({ goals: [] }) } as Response;
      }
      return { ok: true, status: 200, json: async () => MOCK_AGENT } as Response;
    });

    // Mock appendChild/click for download
    const origCreate = document.createElement.bind(document);
    vi.spyOn(document, 'createElement').mockImplementation((tag) => {
      const el = origCreate(tag);
      if (tag === 'a') {
        vi.spyOn(el as HTMLAnchorElement, 'click').mockImplementation(() => {});
      }
      return el;
    });

    renderPage();

    await waitFor(() => screen.getByTestId('export-btn'));
    await user.click(screen.getByTestId('export-btn'));

    await waitFor(() => {
      expect(exportCalled).toBe(true);
    });
  });

  // Test 7: Credentials tab lists credentials
  test('AgentDetailPage credentials tab lists and issues credentials', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/agents/agent-001/credentials')) {
        return new Response(
          JSON.stringify([
            { credential_id: 'cred-1', scopes: ['goals:read'], created_at: '2026-01-01' },
          ]),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (url.includes('/agents/agent-001/versions')) {
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/goals')) {
        return new Response(JSON.stringify({ goals: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/agents/agent-001')) {
        return new Response(
          JSON.stringify({ agent_id: 'agent-001', name: 'TestAgent', autonomy_mode: 'supervised' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response('[]', { status: 200 });
    });

    renderPage('agent-001');

    // Click credentials tab
    await userEvent.click(await screen.findByRole('tab', { name: /credentials/i }));

    expect(await screen.findByText('cred-1')).toBeInTheDocument();
    expect(screen.getByText(/goals:read/)).toBeInTheDocument();
  });
});
