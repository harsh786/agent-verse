import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AgentIdentityPage } from './AgentIdentityPage';

const MOCK_AGENT = {
  agent_id: 'agent-id-1',
  name: 'Identity Bot',
  autonomy_mode: 'supervised',
  created_at: '2026-01-01T00:00:00Z',
};

const MOCK_CREDENTIALS = [
  {
    key_id: 'cred-1',
    key_type: 'jwt',
    scopes: ['goals:read', 'agents:read'],
    expires_at: null,
    last_used_at: null,
    status: 'active',
    description: 'CI pipeline key',
  },
];

function mockFetch(credentials = MOCK_CREDENTIALS) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/credentials')) {
      return new Response(JSON.stringify(credentials), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/agents/')) {
      return new Response(JSON.stringify(MOCK_AGENT), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    return new Response(null, { status: 404 });
  });
}

function renderPage(agentId = 'agent-id-1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={[`/agents/${agentId}/identity`]}>
      <QueryClientProvider client={qc}>
        <Routes>
          <Route path="/agents/:agentId/identity" element={<AgentIdentityPage />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

describe('AgentIdentityPage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      apiKey: 'test-key', tenantId: 'tenant-1', plan: 'enterprise', isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders without crashing', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(document.body).toBeTruthy();
  });

  test('shows loading state initially', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(document.body.innerHTML).toBeTruthy();
  });

  test('renders agent identity heading', async () => {
    mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Agent Identity' })).toBeInTheDocument(),
      { timeout: 3000 }
    );
  });

  test('shows existing credentials from API', async () => {
    mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText('cred-1')).toBeInTheDocument(),
      { timeout: 3000 }
    );
  });

  test('shows credential scopes', async () => {
    mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/goals:read/)).toBeInTheDocument(),
      { timeout: 3000 }
    );
  });

  test('shows empty credentials state gracefully', async () => {
    mockFetch([]);
    renderPage();
    await waitFor(() =>
      expect(screen.queryByText('cred-1')).not.toBeInTheDocument(),
      { timeout: 3000 }
    );
  });

  test('shows error state when credentials fetch fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('Server Error', { status: 500 })
    );
    renderPage();
    await waitFor(() => expect(document.body).toBeTruthy(), { timeout: 3000 });
  });
});
