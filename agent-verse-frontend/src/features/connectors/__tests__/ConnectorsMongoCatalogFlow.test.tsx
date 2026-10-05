/**
 * Register and edit a MongoDB connection from the REAL catalog payload
 * (mongo re-audit A10 / TG-06): Catalog → Configure → Registered page form →
 * POST /connectors, then Edit → PUT /connectors/{id}.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ConnectorsCatalogPage } from '../ConnectorsCatalogPage';
import { ConnectorsRegisteredPage } from '../ConnectorsRegisteredPage';
import { MONGODB_CATALOG_ENTRY, MONGODB_REGISTERED_ROW } from './fixtures/mongodbCatalog';
// Shared with agent-verse-backend/tests/api/test_connectors_fe_fixtures.py, which
// POSTs this exact body and asserts a working built-in MongoDB connection.
import sharedRequest from '@/test/fixtures/mongo_register_request.json';

function renderApp(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/connectors/catalog" element={<ConnectorsCatalogPage />} />
          <Route path="/connectors" element={<ConnectorsRegisteredPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function mockBackend(registered: unknown[]) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init as RequestInit | undefined)?.method ?? 'GET';
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/connectors/catalog')) return json([MONGODB_CATALOG_ENTRY]);
    if (url.endsWith('/connectors') && method === 'POST') return json({ ...MONGODB_REGISTERED_ROW, name: 'mongodb' }, 201);
    if (method === 'PUT') return json(MONGODB_REGISTERED_ROW);
    if (url.endsWith('/connectors')) return json(registered);
    return json({});
  });
}

const bodyOf = (spy: ReturnType<typeof mockBackend>, method: string) => {
  const call = spy.mock.calls.find(([, i]) => (i as RequestInit | undefined)?.method === method);
  return call ? JSON.parse(String((call[1] as RequestInit).body)) : undefined;
};

beforeEach(() => {
  vi.restoreAllMocks();
  useAuthStore.setState({ apiKey: 'av-test-key', tenantId: 'tenant-1', plan: 'free', isAuthenticated: true });
});

describe('MongoDB from the real catalog payload (A10 / TG-06)', () => {
  it('registers: connection_string type, masked single URI, builtin type, no plaintext url', async () => {
    const spy = mockBackend([]);
    renderApp('/connectors/catalog');
    await screen.findByText('MongoDB');
    await userEvent.click(screen.getByRole('button', { name: /^configure$/i }));

    const modal = await screen.findByTestId('register-modal');
    const select = within(modal).getByRole('combobox', { name: /auth type/i }) as HTMLSelectElement;
    expect(select.value).toBe('connection_string');
    expect(within(modal).queryByLabelText(/mongodb uri/i)).not.toBeInTheDocument();
    const uri = within(modal).getByLabelText(/connection uri/i);
    expect(uri).toHaveAttribute('type', 'password');
    expect(within(modal).getByRole('checkbox', { name: /use tls/i })).toBeInTheDocument();

    await userEvent.type(uri, 'mongodb+srv://cluster0.example.mongodb.net/');
    await userEvent.type(within(modal).getByLabelText(/^username/i), 'alice');
    await userEvent.type(within(modal).getByLabelText(/^password/i), 'S3cret');
    await userEvent.click(within(modal).getByRole('button', { name: /^register$/i }));

    await waitFor(() => expect(bodyOf(spy, 'POST')).toBeTruthy());
    const body = bodyOf(spy, 'POST');
    expect(body).toMatchObject({
      name: 'mongodb',
      type: 'builtin-mongodb',
      auth_type: 'connection_string',
      url: 'builtin://',
      auth_config: { url: 'mongodb+srv://cluster0.example.mongodb.net/', username: 'alice', password: 'S3cret' },
    });
  });

  it('edits: shows the stored fields (secrets masked) and sends masked values back unchanged', async () => {
    const spy = mockBackend([MONGODB_REGISTERED_ROW]);
    renderApp('/connectors');
    await screen.findByRole('link', { name: 'orders-db' });
    const row = screen.getByRole('link', { name: 'orders-db' }).closest('tr') as HTMLElement;
    expect(row).toHaveTextContent('mongodb://8.8.8.8:27017/shop'); // display_url
    expect(row).toHaveTextContent('Connection String');
    await userEvent.click(within(row).getByRole('button', { name: /^edit$/i }));

    const modal = screen.getByTestId('register-modal');
    expect((within(modal).getByRole('combobox', { name: /auth type/i }) as HTMLSelectElement).value).toBe('connection_string');
    expect(within(modal).getByLabelText(/connection uri/i)).toHaveAttribute('type', 'password');
    expect(within(modal).getByLabelText(/^database/i)).toHaveValue('shop');
    const user = within(modal).getByLabelText(/^username/i);
    await userEvent.clear(user);
    await userEvent.type(user, 'bob');
    await userEvent.click(within(modal).getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(bodyOf(spy, 'PUT')).toBeTruthy());
    const body = bodyOf(spy, 'PUT');
    expect(body).toMatchObject({
      auth_type: 'connection_string',
      url: 'builtin://',
      auth_config: { url: '<redacted>', username: 'bob', password: '<redacted>', database: 'shop', auth_source: 'admin', auth_mechanism: 'SCRAM-SHA-256' },
    });
    expect(body).not.toHaveProperty('type');
  });
});

describe('NF-3 the register form produces exactly the shared backend-tested body', () => {
  it('mongo_register_request.json — the body agent-verse-backend POSTs and proves working', async () => {
    const spy = mockBackend([]);
    renderApp('/connectors/catalog');
    await screen.findByText('MongoDB');
    await userEvent.click(screen.getByRole('button', { name: /^configure$/i }));
    const modal = await screen.findByTestId('register-modal');
    const cfg = sharedRequest.auth_config;
    const name = within(modal).getByLabelText(/^name/i);
    await userEvent.clear(name);
    await userEvent.type(name, sharedRequest.name);
    await userEvent.type(within(modal).getByLabelText(/connection uri/i), cfg.url);
    await userEvent.type(within(modal).getByLabelText(/^database/i), cfg.database);
    await userEvent.type(within(modal).getByLabelText(/^username/i), cfg.username);
    await userEvent.type(within(modal).getByLabelText(/^password/i), cfg.password);
    await userEvent.type(within(modal).getByLabelText(/auth source/i), cfg.auth_source);
    await userEvent.selectOptions(within(modal).getByLabelText(/auth mechanism/i), cfg.auth_mechanism);
    await userEvent.click(within(modal).getByRole('button', { name: /^register$/i }));
    await waitFor(() => expect(bodyOf(spy, 'POST')).toBeTruthy());
    expect(bodyOf(spy, 'POST')).toEqual(sharedRequest);
  });
});
