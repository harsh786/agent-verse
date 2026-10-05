/** ConnectorDetailPage with MongoDB connection strings (mongo re-audit A7, A9). */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
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
