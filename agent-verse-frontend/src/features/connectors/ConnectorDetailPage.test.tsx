import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
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
    expect(screen.getByText('Recent goals using this connector')).toBeInTheDocument();
  });

  test('Usage tab colors success rate amber/red for mid/low values and reflects goal status', async () => {
    mockFetch({
      usage: {
        goals: [
          { id: 'g2', goal: 'Amber goal', status: 'running' },
          { id: 'g3', goal: 'Failed goal', status: 'failed' },
        ],
        total: 2,
        success_rate: 65,
        filtered: true,
      },
    });
    renderPage();
    await screen.findByRole('heading', { name: 'GitHub' });
    await userEvent.click(screen.getByRole('tab', { name: 'Usage' }));
    const rate = await screen.findByText('65%');
    expect(rate.className).toContain('text-amber-600');
    const failedRow = screen.getByText('Failed goal').closest('a');
    expect(failedRow).not.toBeNull();
    expect(within(failedRow as HTMLElement).getByText('failed').className).toContain('bg-red-100');
    const runningRow = screen.getByText('Amber goal').closest('a');
    expect(within(runningRow as HTMLElement).getByText('running').className).toContain('bg-yellow-100');
  });

  test('Usage tab colors success rate red when below 60', async () => {
    mockFetch({
      usage: { goals: [{ id: 'g4', goal: 'Low goal', status: 'complete' }], total: 1, success_rate: 20, filtered: true },
    });
    renderPage();
    await screen.findByRole('heading', { name: 'GitHub' });
    await userEvent.click(screen.getByRole('tab', { name: 'Usage' }));
    const rate = await screen.findByText('20%');
    expect(rate.className).toContain('text-red-600');
  });

  test('Exposed Tools shows a loading skeleton before tools resolve', async () => {
    let resolveTools: (v: unknown) => void = () => {};
    const toolsPromise = new Promise((resolve) => {
      resolveTools = resolve;
    });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      const json = (body: unknown) =>
        new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/tools')) {
        await toolsPromise;
        return json(TOOLS);
      }
      if (/\/connectors\/[^/?]+$/.test(url)) return json(CONNECTOR);
      return json({});
    });
    const { container } = renderPage();
    await screen.findByRole('heading', { name: 'GitHub' });
    expect(container.querySelector('.animate-pulse')).not.toBeNull();
    resolveTools(TOOLS);
    expect(await screen.findByText('github_create_pr')).toBeInTheDocument();
  });

  test('Edit Credentials navigates back to the connectors list with editConnectorId state', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: 'GitHub' });
    await userEvent.click(screen.getByRole('button', { name: /Edit Credentials/i }));
    expect(await screen.findByText('Connectors list')).toBeInTheDocument();
  });

  test('Test Connection header action shows the unreachable state and an error toast', async () => {
    mockFetch({ test: { reachable: false, error: 'timeout contacting host' } });
    renderPage();
    await screen.findByRole('heading', { name: 'GitHub' });
    await userEvent.click(screen.getByRole('button', { name: /Test connector connection/i }));
    const banner = await screen.findByText(/Unreachable: timeout contacting host/i);
    expect(banner.className).toContain('bg-red-50');
    expect(
      useToastStore.getState().toasts.some((t) => t.kind === 'error' && t.message.includes('timeout contacting host')),
    ).toBe(true);
  });

  test('Test Connection header action falls back to a default error message when none is provided', async () => {
    mockFetch({ test: { reachable: false } });
    renderPage();
    await screen.findByRole('heading', { name: 'GitHub' });
    await userEvent.click(screen.getByRole('button', { name: /Test connector connection/i }));
    expect(await screen.findByText(/Unreachable: unknown error/i)).toBeInTheDocument();
  });

  test('Test Connection header action reports a network failure via onError', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = ((init as RequestInit | undefined)?.method ?? 'GET').toUpperCase();
      const json = (body: unknown) =>
        new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/test') && method === 'POST') throw new Error('network down');
      if (url.includes('/tools')) return json(TOOLS);
      if (/\/connectors\/[^/?]+$/.test(url)) return json(CONNECTOR);
      return json({});
    });
    renderPage();
    await screen.findByRole('heading', { name: 'GitHub' });
    await userEvent.click(screen.getByRole('button', { name: /Test connector connection/i }));
    await waitFor(() =>
      expect(
        useToastStore.getState().toasts.some((t) => t.kind === 'error' && t.message.includes('Test failed')),
      ).toBe(true),
    );
  });

  test('renders fallback connector name and auth/server placeholders when fields are missing', async () => {
    mockFetch({ connector: { server_id: null, name: null, url: null, status: null, auth_type: null } });
    renderPage('c9');
    expect(await screen.findByRole('heading', { name: 'c9' })).toBeInTheDocument();
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  test('Health tab shows the failed state with detail and error text', async () => {
    mockFetch({ test: { reachable: false, error: 'bad credentials' } });
    renderPage();
    await screen.findByRole('heading', { name: 'GitHub' });
    await userEvent.click(screen.getByRole('tab', { name: 'Health' }));
    expect(await screen.findByText('Connection Status')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Test Connection' }));
    expect(await screen.findByText('Connection failed')).toBeInTheDocument();
    expect(screen.getByText('bad credentials')).toBeInTheDocument();
    expect(
      useToastStore.getState().toasts.some((t) => t.kind === 'error' && t.message === 'bad credentials'),
    ).toBe(true);
  });

  test('Health tab falls back to the default error toast message when the test result has none', async () => {
    mockFetch({ test: { reachable: false } });
    renderPage();
    await screen.findByRole('heading', { name: 'GitHub' });
    await userEvent.click(screen.getByRole('tab', { name: 'Health' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Test Connection' }));
    expect(await screen.findByText('Connection failed')).toBeInTheDocument();
    expect(
      useToastStore.getState().toasts.some((t) => t.kind === 'error' && t.message === 'Connection test failed'),
    ).toBe(true);
  });

  test('Health tab reports a network failure via onError', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = ((init as RequestInit | undefined)?.method ?? 'GET').toUpperCase();
      const json = (body: unknown) =>
        new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/test') && method === 'POST') throw new Error('offline');
      if (url.includes('/tools')) return json(TOOLS);
      if (/\/connectors\/[^/?]+$/.test(url)) return json(CONNECTOR);
      return json({});
    });
    renderPage();
    await screen.findByRole('heading', { name: 'GitHub' });
    await userEvent.click(screen.getByRole('tab', { name: 'Health' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Test Connection' }));
    await waitFor(() =>
      expect(
        useToastStore.getState().toasts.some((t) => t.kind === 'error' && t.message.includes('Test failed')),
      ).toBe(true),
    );
  });

  test('Health tab shows mcp_url and latency when present on a successful test', async () => {
    mockFetch({ test: { reachable: true, latency_ms: 7, mcp_url: 'https://mcp.example/endpoint' } });
    renderPage();
    await screen.findByRole('heading', { name: 'GitHub' });
    await userEvent.click(screen.getByRole('tab', { name: 'Health' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Test Connection' }));
    expect(await screen.findByText('Connection successful')).toBeInTheDocument();
    expect(screen.getByText('7 ms')).toBeInTheDocument();
    expect(screen.getByText('https://mcp.example/endpoint')).toBeInTheDocument();
  });

  test('Health tab falls back to connector.test_result and shows last tested time when no live test has run', async () => {
    mockFetch({
      connector: {
        ...CONNECTOR,
        last_tested: '2024-01-01T00:00:00.000Z',
        test_result: { success: true, detail: 'Authenticated previously' },
      },
    });
    renderPage();
    await screen.findByRole('heading', { name: 'GitHub' });
    await userEvent.click(screen.getByRole('tab', { name: 'Health' }));
    expect(await screen.findByText(/Last tested/i)).toBeInTheDocument();
    expect(screen.getByText('Connection successful')).toBeInTheDocument();
    expect(screen.getByText('Authenticated previously')).toBeInTheDocument();
  });
});
