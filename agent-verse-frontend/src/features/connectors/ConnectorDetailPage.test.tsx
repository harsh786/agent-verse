import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ConnectorDetailPage } from './ConnectorDetailPage';

const CONNECTOR = {
  server_id: 'srv1', name: 'GitHub', url: 'https://mcp.github.example',
  status: 'active', auth_type: 'oauth', last_tested: null, test_result: null,
};

const TOOLS = [
  { name: 'github_create_pr', description: 'Open a pull request' },
  { name: 'github_list_issues', description: 'List issues' },
];

interface MockOpts {
  connector?: unknown;
  tools?: unknown[];
  usage?: unknown;
  test?: unknown;
}

function mockFetch(opts: MockOpts = {}) {
  const {
    connector = CONNECTOR,
    tools = TOOLS,
    usage = { goals: [{ id: 'g1', goal: 'Ship the release', status: 'complete' }], total: 3, success_rate: 80, filtered: true },
    test: testResult = { reachable: true, latency_ms: 42, detail: 'Authenticated as @octocat' },
  } = opts;
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = ((init as RequestInit | undefined)?.method ?? 'GET').toUpperCase();
    const json = (body: unknown) =>
      new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

    if (url.includes('/test') && method === 'POST') return json(testResult);
    if (/\/connectors\/[^/?]+$/.test(url) && method === 'DELETE') return json({});
    if (url.includes('/tools')) return json(tools);
    if (url.includes('/usage')) return json(usage);
    if (/\/connectors\/[^/?]+$/.test(url)) return json(connector);
    return json({});
  });
}

function renderPage(id = 'c1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/connectors/${id}`]}>
        <Routes>
          <Route path="/connectors/:connectorId" element={<ConnectorDetailPage />} />
          <Route path="/connectors" element={<div>Connectors list</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ConnectorDetailPage', () => {
  test('renders the connector title and overview info', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByRole('heading', { name: 'GitHub' })).toBeInTheDocument();
    // URL appears in both the subtitle and the Connector Info list.
    expect(screen.getAllByText('https://mcp.github.example').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Connector Info')).toBeInTheDocument();
  });

  test('lists exposed tools on the Overview tab', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByText('github_create_pr')).toBeInTheDocument();
    expect(screen.getByText('Open a pull request')).toBeInTheDocument();
  });

  test('shows the empty tools state when none are discovered', async () => {
    mockFetch({ tools: [] });
    renderPage();
    await screen.findByRole('heading', { name: 'GitHub' });
    expect(await screen.findByText(/No tools discovered/i)).toBeInTheDocument();
  });

  test('renders the not-found state when the connector is missing', async () => {
    mockFetch({ connector: null });
    renderPage();
    expect(await screen.findByText(/Connector not found/i)).toBeInTheDocument();
  });

  test('Test Connection header action POSTs to /connectors/:id/test and shows the result', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: 'GitHub' });
    await userEvent.click(screen.getByRole('button', { name: /Test connector connection/i }));
    expect(await screen.findByText(/Reachable \(42ms\)/i)).toBeInTheDocument();
    expect(spy.mock.calls.some(([u, i]) =>
      String(u).includes('/connectors/c1/test') && (i as RequestInit)?.method === 'POST')).toBe(true);
  });

  test('Remove action confirms and DELETEs the connector', async () => {
    const spy = mockFetch();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderPage();
    await screen.findByRole('heading', { name: 'GitHub' });
    await userEvent.click(screen.getByRole('button', { name: /Remove connector/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/connectors/c1') && (i as RequestInit)?.method === 'DELETE'),
      ).toBe(true),
    );
  });

  test('Remove action does nothing when the confirm is cancelled', async () => {
    const spy = mockFetch();
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderPage();
    await screen.findByRole('heading', { name: 'GitHub' });
    await userEvent.click(screen.getByRole('button', { name: /Remove connector/i }));
    expect(spy.mock.calls.some(([u, i]) =>
      String(u).includes('/connectors/c1') && (i as RequestInit)?.method === 'DELETE')).toBe(false);
  });

  test('Health tab runs a connection test and renders success detail', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: 'GitHub' });
    await userEvent.click(screen.getByRole('tab', { name: 'Health' }));
    expect(await screen.findByText('Connection Status')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Test Connection' }));
    expect(await screen.findByText(/Connection successful/i)).toBeInTheDocument();
    expect(screen.getByText(/Authenticated as @octocat/i)).toBeInTheDocument();
  });

  test('Usage tab renders goal stats and rows', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: 'GitHub' });
    await userEvent.click(screen.getByRole('tab', { name: 'Usage' }));
    expect(await screen.findByText('Total Goals')).toBeInTheDocument();
    expect(screen.getByText('Ship the release')).toBeInTheDocument();
    expect(screen.getByText('80%')).toBeInTheDocument();
  });

  test('Usage tab shows an empty state when there are no goals', async () => {
    mockFetch({ usage: { goals: [], total: 0, success_rate: null, filtered: false } });
    renderPage();
    await screen.findByRole('heading', { name: 'GitHub' });
    await userEvent.click(screen.getByRole('tab', { name: 'Usage' }));
    expect(await screen.findByText(/No goals found for this connector/i)).toBeInTheDocument();
  });
});
