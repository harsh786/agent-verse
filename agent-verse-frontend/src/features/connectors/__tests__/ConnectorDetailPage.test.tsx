import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ConnectorDetailPage } from '../ConnectorDetailPage';

function renderPage(connectorId = 'conn-1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/connectors/${connectorId}`]}>
        <Routes>
          <Route path="/connectors/:connectorId" element={<ConnectorDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const CONNECTOR_DETAIL = {
  server_id: 'conn-1',
  name: 'my-github',
  url: 'http://localhost:9001',
  auth_type: 'bearer',
  status: 'active',
};

function mockFetchConnector() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/connectors/conn-1/tools')) {
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (url.includes('/connectors/conn-1')) {
      return new Response(JSON.stringify(CONNECTOR_DETAIL), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't1', plan: 'free', isAuthenticated: true });
});

describe('ConnectorDetailPage', () => {
  test('renders connector detail with connector name as heading', async () => {
    mockFetchConnector();
    renderPage();
    expect(await screen.findByText('my-github')).toBeInTheDocument();
  });

  test('shows connector URL from fetched data', async () => {
    mockFetchConnector();
    renderPage();
    // URL appears in both the subtitle and the info card — use getAllByText
    const urlElements = await screen.findAllByText('http://localhost:9001');
    expect(urlElements.length).toBeGreaterThanOrEqual(1);
  });

  test('shows connection status from fetched data', async () => {
    mockFetchConnector();
    renderPage();
    // status badge and info row both show 'active'
    const statusElements = await screen.findAllByText('active');
    expect(statusElements.length).toBeGreaterThanOrEqual(1);
  });

  test('Test Connection button triggers POST /connectors/{id}/test', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/connectors/conn-1/test') && (init as RequestInit)?.method === 'POST') {
        return new Response(
          JSON.stringify({ reachable: true, latency_ms: 55, server_id: 'conn-1' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.includes('/connectors/conn-1/tools')) {
        return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      if (url.includes('/connectors/conn-1')) {
        return new Response(JSON.stringify(CONNECTOR_DETAIL), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    renderPage();
    await screen.findByText('my-github');

    // Button text is "Test Connection"; aria-label is "Test connector connection"
    const testBtn = screen.getByRole('button', { name: /test connector connection/i });
    await userEvent.click(testBtn);

    await waitFor(() =>
      expect(fetchSpy.mock.calls.some(([u, i]) =>
        String(u).includes('/connectors/conn-1/test') && (i as RequestInit)?.method === 'POST',
      )).toBe(true),
    );
  });

  test('shows test result after successful test', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/connectors/conn-1/test') && (init as RequestInit)?.method === 'POST') {
        return new Response(
          JSON.stringify({ reachable: true, latency_ms: 55, server_id: 'conn-1' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.includes('/connectors/conn-1/tools')) {
        return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      if (url.includes('/connectors/conn-1')) {
        return new Response(JSON.stringify(CONNECTOR_DETAIL), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    renderPage();
    await screen.findByText('my-github');
    await userEvent.click(screen.getByRole('button', { name: /test connector connection/i }));

    await waitFor(() => expect(screen.getByText(/Reachable/i)).toBeInTheDocument());
  });

  test('shows "Connector not found" when API returns 404', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/connectors/conn-1/tools')) {
        return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response('Not Found', { status: 404, statusText: 'Not Found' });
    });

    renderPage();
    expect(await screen.findByText(/Connector not found/i)).toBeInTheDocument();
  });

  test('renders Back to Connectors navigation link', async () => {
    mockFetchConnector();
    renderPage();
    expect(await screen.findByText(/Back to Connectors/i)).toBeInTheDocument();
  });
});
