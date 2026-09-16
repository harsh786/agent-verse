import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { WebhookManager } from './WebhookManager';

const WEBHOOKS = [
  { id: 'w1', name: 'Deploy notifications', url: 'https://hooks.example.com/deploy', events: ['deploy', 'rollback'], active: true, delivery_success_rate: 0.98 },
  { id: 'w2', name: 'Alert sink', url: 'https://hooks.example.com/alert', events: ['*'], active: false },
];

function mockWebhooks(list: unknown[] = WEBHOOKS) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/gateway/webhooks') && method === 'GET')
      return new Response(JSON.stringify({ data: list }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderManager() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><WebhookManager orgId="org-1" /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('WebhookManager', () => {
  test('renders webhook cards with the name and destination URL', async () => {
    mockWebhooks();
    renderManager();
    expect(await screen.findByText('Deploy notifications')).toBeInTheDocument();
    expect(screen.getByText('https://hooks.example.com/deploy')).toBeInTheDocument();
    expect(screen.getByText('Alert sink')).toBeInTheDocument();
  });

  test('shows the empty state when there are no webhooks', async () => {
    mockWebhooks([]);
    renderManager();
    expect(await screen.findByText('No webhooks configured.')).toBeInTheDocument();
  });

  test('adding a webhook POSTs the name and url', async () => {
    const spy = mockWebhooks([]);
    renderManager();
    await screen.findByText('No webhooks configured.');
    await userEvent.click(screen.getByRole('button', { name: /Add webhook/i }));
    await userEvent.type(screen.getByPlaceholderText('Deploy notifications'), 'CI hook');
    await userEvent.type(screen.getByPlaceholderText('https://your-server.com/webhook'), 'https://ci.example.com/hook');
    await userEvent.click(screen.getByRole('button', { name: /Create Webhook/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => {
        if (!String(u).includes('/gateway/webhooks') || (i as RequestInit)?.method !== 'POST') return false;
        const body = JSON.parse((i as RequestInit).body as string);
        return body.name === 'CI hook' && body.url === 'https://ci.example.com/hook';
      })).toBe(true),
    );
  });

  test('deleting a webhook issues a DELETE for its id', async () => {
    const spy = mockWebhooks();
    renderManager();
    await screen.findByText('Deploy notifications');
    await userEvent.click(screen.getByRole('button', { name: 'Delete webhook Deploy notifications' }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/gateway/webhooks/w1') && (i as RequestInit)?.method === 'DELETE')).toBe(true),
    );
  });
});
