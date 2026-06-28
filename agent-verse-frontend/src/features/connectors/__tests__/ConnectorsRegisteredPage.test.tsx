import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ConnectorsRegisteredPage } from '../ConnectorsRegisteredPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ConnectorsRegisteredPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const REGISTERED_CONNECTORS = [
  {
    server_id: 'conn-1',
    name: 'my-github',
    url: 'http://localhost:9001',
    auth_type: 'bearer',
    status: 'active',
  },
  {
    server_id: 'conn-2',
    name: 'my-slack',
    url: 'http://localhost:9002',
    auth_type: 'api_key',
    status: 'active',
  },
];

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't1', plan: 'free', isAuthenticated: true });
});

describe('ConnectorsRegisteredPage', () => {
  test('renders Registered Connectors heading', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(REGISTERED_CONNECTORS), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    renderPage();
    expect(await screen.findByText(/Registered Connectors/i)).toBeInTheDocument();
  });

  test('shows connector name and URL for each registered connector', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(REGISTERED_CONNECTORS), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    renderPage();
    expect(await screen.findByText('my-github')).toBeInTheDocument();
    expect(screen.getByText('my-slack')).toBeInTheDocument();
    expect(screen.getByText('http://localhost:9001')).toBeInTheDocument();
  });

  test('Remove button triggers DELETE /connectors/{id}', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/connectors/conn-1') && (init as RequestInit)?.method === 'DELETE') {
        return new Response(null, { status: 204 });
      }
      // Re-fetch after delete returns empty list
      if (url.includes('/connectors') && !(init as RequestInit)?.method) {
        return new Response(JSON.stringify(REGISTERED_CONNECTORS), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    renderPage();
    await screen.findByText('my-github');

    // Click first Remove button (for conn-1)
    const removeButtons = screen.getAllByRole('button', { name: /remove/i });
    await userEvent.click(removeButtons[0]);

    await waitFor(() =>
      expect(fetchSpy.mock.calls.some(([u, i]) =>
        String(u).includes('/connectors/conn-1') && (i as RequestInit)?.method === 'DELETE',
      )).toBe(true),
    );
  });

  test('Test button triggers POST /connectors/{id}/test and shows result', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/connectors/conn-1/test') && (init as RequestInit)?.method === 'POST') {
        return new Response(
          JSON.stringify({ reachable: true, status: 'ok', latency_ms: 42 }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      return new Response(JSON.stringify(REGISTERED_CONNECTORS), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    });

    renderPage();
    await screen.findByText('my-github');

    const testButtons = screen.getAllByRole('button', { name: /^test$/i });
    await userEvent.click(testButtons[0]);

    await waitFor(() =>
      expect(fetchSpy.mock.calls.some(([u, i]) =>
        String(u).includes('/connectors/conn-1/test') && (i as RequestInit)?.method === 'POST',
      )).toBe(true),
    );

    // Status result should appear in the table
    await waitFor(() => expect(screen.getByText('ok')).toBeInTheDocument());
  });

  test('shows empty state when no connectors registered', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    renderPage();
    expect(await screen.findByText(/No connectors registered yet/i)).toBeInTheDocument();
  });

  test('+ Register Connector button opens registration modal', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    renderPage();
    await screen.findByText(/Registered Connectors/i);
    const registerBtn = screen.getByRole('button', { name: /\+ Register Connector/i });
    await userEvent.click(registerBtn);
    expect(await screen.findByText(/Register MCP Connector/i)).toBeInTheDocument();
  });
});
