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

  test('treats a payload with no services array as empty', async () => {
    // Response with no `services` key -> the component falls back to [] and,
    // once loading finishes, renders the empty message rather than crashing.
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ error: 'boom' }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    render(<ConnectedServicesPanel />);
    expect(await screen.findByText(/No services connected/i)).toBeInTheDocument();
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
    // The newly added (pending) service appears in the list.
    expect(await screen.findByText('Notion MCP')).toBeInTheDocument();
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
