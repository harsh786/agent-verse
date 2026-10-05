/** ConnectorDetailPage with MongoDB connection strings (mongo re-audit A7, A9). */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { ConnectorDetailPage } from '../ConnectorDetailPage';

const ID = 'builtin-mongodb:orders-db';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/connectors/${encodeURIComponent(ID)}`]}>
        <Routes>
          <Route path="/connectors/:connectorId" element={<ConnectorDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function mockFetch(connector: unknown, test?: { status: number; body: unknown }) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
    if (url.endsWith('/test') && (init as RequestInit)?.method === 'POST' && test) return json(test.body, test.status);
    if (url.includes('/tools')) return json([]);
    if (url.includes('/usage')) return json({ goals: [], total: 0 });
    if (url.includes(`/connectors/${encodeURIComponent(ID)}`)) return json(connector);
    return json({});
  });
}

beforeEach(() => {
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't1', plan: 'free', isAuthenticated: true });
});

describe('A7 connector detail never shows a DSN password', () => {
  test('a plaintext DSN url is shown with its userinfo masked, not as a link', async () => {
    mockFetch({ server_id: ID, name: 'orders-db', url: 'mongodb://alice:S3cretPw@db.example.com:27017/orders', auth_type: 'connection_string', status: 'active' });
    renderPage();
    expect(await screen.findAllByText('mongodb://***@db.example.com:27017/orders')).not.toHaveLength(0);
    expect(document.body).not.toHaveTextContent('S3cretPw');
    expect(document.querySelector('a[href^="mongodb"]')).toBeNull();
  });

  test('a built-in connection shows its configured host (masked) instead of builtin://', async () => {
    mockFetch({ server_id: ID, name: 'orders-db', url: 'builtin://', upstream_url: 'mongodb+srv://bob:Pw2@cluster0.example.net/', auth_type: 'connection_string', status: 'active' });
    renderPage();
    expect(await screen.findAllByText('mongodb+srv://***@cluster0.example.net/')).not.toHaveLength(0);
    expect(document.body).not.toHaveTextContent('Pw2');
  });
});

describe('A9 connector detail test errors are friendly', () => {
  const CONN = { server_id: ID, name: 'orders-db', url: 'builtin://', auth_type: 'connection_string', status: 'active' };
  const RAW = "cluster0-shard-00-01.abcd.mongodb.net:27017: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed, Timeout: 5.0s, Topology Description: <...>";

  beforeEach(() => useToastStore.setState({ toasts: [] }));

  test('Health tab: a failed result shows a short reason, raw text only behind Details', async () => {
    mockFetch(CONN, { status: 200, body: { server_id: ID, reachable: false, status: 'failed', error: RAW } });
    renderPage();
    await userEvent.click(await screen.findByRole('tab', { name: /^health$/i }));
    await userEvent.click(screen.getByRole('button', { name: /^test connection$/i }));
    expect(await screen.findByText(/tls handshake failed/i)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent('abcd.mongodb.net');
    await userEvent.click(screen.getByRole('button', { name: /details/i }));
    expect(screen.getByText(/CERTIFICATE_VERIFY_FAILED/)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent('abcd.mongodb.net');
    await waitFor(() => expect(useToastStore.getState().toasts.map((t) => t.message).join(' ')).toMatch(/tls handshake failed/i));
    expect(useToastStore.getState().toasts.map((t) => t.message).join(' ')).not.toContain('mongodb.net');
  });

  test('a 4xx test request toasts a friendly reason, not "Error: ..."', async () => {
    mockFetch(CONN, { status: 400, body: { detail: 'SSRF protection: disallowed URL' } });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: /test connector connection/i }));
    await waitFor(() => expect(useToastStore.getState().toasts.some((t) => /address is blocked/i.test(t.message))).toBe(true));
    expect(useToastStore.getState().toasts.some((t) => t.message.includes('Error:') || t.message.includes('[object'))).toBe(false);
  });
});
