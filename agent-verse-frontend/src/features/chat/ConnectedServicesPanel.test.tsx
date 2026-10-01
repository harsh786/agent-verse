import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ConnectedServicesPanel } from './ConnectedServicesPanel';

const SERVICES = {
  services: [
    {
      id: 'svc-1',
      name: 'GitHub MCP',
      url: 'https://mcp.github.example/sse',
      scopes: ['repo'],
      status: 'connected',
      connected_at: '2026-01-01T00:00:00Z',
    },
    {
      id: 'svc-2',
      name: 'Slack MCP',
      url: 'https://mcp.slack.example/sse',
      scopes: [],
      status: 'pending',
      connected_at: null,
    },
  ],
};

function mockFetch(services = SERVICES) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/chat/services') && method === 'DELETE')
      return new Response(JSON.stringify({ status: 'deleted' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    if (url.includes('/chat/services') && method === 'POST')
      return new Response(JSON.stringify({ service_id: 'svc-new', status: 'pending', oauth_url: 'https://auth.example' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    if (url.includes('/chat/services'))
      return new Response(JSON.stringify(services), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ConnectedServicesPanel', () => {
  test('renders the loaded services with their names and statuses', async () => {
    mockFetch();
    render(<ConnectedServicesPanel />);
    expect(screen.getByRole('heading', { name: 'Connected Services' })).toBeInTheDocument();

    expect(await screen.findByText('GitHub MCP')).toBeInTheDocument();
    expect(screen.getByText('Slack MCP')).toBeInTheDocument();
    // 'connected' shows verbatim; 'pending' is relabelled to "authorizing…".
    expect(screen.getByText('connected')).toBeInTheDocument();
    expect(screen.getByText('authorizing…')).toBeInTheDocument();
  });

  test('shows the empty state when no services are connected', async () => {
    mockFetch({ services: [] });
    render(<ConnectedServicesPanel />);
    expect(
      await screen.findByText(/No services connected\. Add an MCP tool to extend agent capabilities\./i),
    ).toBeInTheDocument();
  });

  test('a failing list renders an error with retry, not "No services connected"', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Service registry unavailable' }), {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    render(<ConnectedServicesPanel />);
    expect(await screen.findByRole('alert')).toHaveTextContent(/Service registry unavailable/);
    expect(screen.queryByText(/No services connected/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument();
  });

  test('a failed DELETE keeps the row and shows the error', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      if ((init?.method ?? 'GET').toUpperCase() === 'DELETE')
        return new Response(JSON.stringify({ detail: 'Service not found' }), {
          status: 404, headers: { 'Content-Type': 'application/json' },
        });
      return new Response(JSON.stringify(SERVICES), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    render(<ConnectedServicesPanel />);
    await screen.findByText('GitHub MCP');
    fireEvent.click(screen.getByRole('button', { name: 'Disconnect GitHub MCP' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/Service not found/);
    expect(screen.getByText('GitHub MCP')).toBeInTheDocument();
  });

  test('a refused connect shows the error and keeps the form open', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      if ((init?.method ?? 'GET').toUpperCase() === 'POST')
        return new Response(JSON.stringify({ detail: 'OAuth for this server is not supported' }), {
          status: 501, headers: { 'Content-Type': 'application/json' },
        });
      return new Response(JSON.stringify({ services: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    render(<ConnectedServicesPanel />);
    await screen.findByText(/No services connected/i);
    fireEvent.click(screen.getByRole('button', { name: /Add service/i }));
    fireEvent.change(screen.getByPlaceholderText('Service name'), { target: { value: 'Notion MCP' } });
    fireEvent.change(screen.getByPlaceholderText('MCP server URL'), { target: { value: 'https://mcp.notion.example/sse' } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/not supported/);
    expect(screen.getByPlaceholderText('Service name')).toBeInTheDocument();
    expect(screen.queryByText('Notion MCP')).not.toBeInTheDocument();
  });

  test('disconnect fires a DELETE for the chosen service and removes the row', async () => {
    const spy = mockFetch();
    render(<ConnectedServicesPanel />);
    await screen.findByText('GitHub MCP');

    fireEvent.click(screen.getByRole('button', { name: 'Disconnect GitHub MCP' }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) => String(u).includes('/chat/services/svc-1') && (i as RequestInit)?.method === 'DELETE',
        ),
      ).toBe(true),
    );
    await waitFor(() => expect(screen.queryByText('GitHub MCP')).not.toBeInTheDocument());
  });

  test('adding a service POSTs the new name + url', async () => {
    const spy = mockFetch();
    render(<ConnectedServicesPanel />);
    await screen.findByText('GitHub MCP');

    fireEvent.click(screen.getByRole('button', { name: /Add service/i }));
    fireEvent.change(screen.getByPlaceholderText('Service name'), { target: { value: 'Notion MCP' } });
    fireEvent.change(screen.getByPlaceholderText('MCP server URL'), {
      target: { value: 'https://mcp.notion.example/sse' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));

    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => {
          const init = i as RequestInit;
          return (
            String(u).includes('/chat/services') &&
            init?.method === 'POST' &&
            typeof init?.body === 'string' &&
            init.body.includes('Notion MCP') &&
            init.body.includes('https://mcp.notion.example/sse')
          );
        }),
      ).toBe(true),
    );
    // The newly added (pending) service appears in the list, with the
    // authorization link the backend returned (the user must finish OAuth).
    expect(await screen.findByText('Notion MCP')).toBeInTheDocument();
    const link = screen.getByRole('link', { name: /Authorize Notion MCP/i });
    expect(link).toHaveAttribute('href', 'https://auth.example');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link.getAttribute('rel')).toContain('noopener');
  });

  test('a pending service is re-checked so a finished OAuth shows as connected', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let calls = 0;
      vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
        calls += 1;
        const status = calls === 1 ? 'pending' : 'connected';
        return new Response(JSON.stringify({ services: [{ ...SERVICES.services[1], status }] }), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      });
      render(<ConnectedServicesPanel />);
      expect(await screen.findByText('authorizing…')).toBeInTheDocument();
      await vi.advanceTimersByTimeAsync(6000);
      expect(await screen.findByText('connected')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  test('the close button fires the onClose callback', async () => {
    mockFetch();
    const onClose = vi.fn();
    render(<ConnectedServicesPanel onClose={onClose} />);
    await screen.findByText('GitHub MCP');
    fireEvent.click(screen.getByRole('button', { name: 'Close panel' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
