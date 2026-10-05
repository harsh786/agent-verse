/**
 * MongoDB / connection_string connectors on the Registered Connectors page
 * (mongo re-audit A2, A4, A6, A7, A9, A10/TG-06).
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { ConnectorsRegisteredPage } from '../ConnectorsRegisteredPage';
import { useAuthStore } from '@/stores/auth';

function renderPage(locationState?: unknown) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[{ pathname: '/connectors', state: locationState }]}>
        <ConnectorsRegisteredPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

type Handler = { match: (url: string, init?: RequestInit) => boolean; response: unknown; status?: number };

function mockFetch(handlers: Handler[]) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    for (const h of handlers) {
      if (h.match(url, init as RequestInit)) {
        return new Response(JSON.stringify(h.response), {
          status: h.status ?? 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
    }
    return new Response('[]', { status: 200 });
  });
}

const listOf = (rows: unknown[]): Handler => ({
  match: (u, i) => u.endsWith('/connectors') && (!i?.method || i.method === 'GET'),
  response: rows,
});

const authSelect = () => screen.getByRole('combobox', { name: /auth type/i }) as HTMLSelectElement;
const openRegister = async () =>
  userEvent.click(await screen.findByRole('button', { name: /register connector/i }));

beforeEach(() => {
  vi.restoreAllMocks();
  useAuthStore.setState({ apiKey: 'av-test-key', tenantId: 'tenant-1', plan: 'free', isAuthenticated: true });
});

// ── A2: connection_string auth type ───────────────────────────────────────────

describe('A2 connection_string auth type', () => {
  it('offers a Connection String auth type', async () => {
    mockFetch([listOf([])]);
    renderPage();
    await openRegister();
    expect(within(authSelect()).getByRole('option', { name: /connection string/i })).toHaveValue('connection_string');
  });

  it('a connection_string prefill shows Connection String, not Bearer Token', async () => {
    mockFetch([listOf([])]);
    renderPage({ prefill: { connector_type: 'mongodb', name: 'mongodb', url: 'mongodb://localhost:27017', auth_type: 'connection_string' } });
    await screen.findByTestId('register-modal');
    expect(authSelect().value).toBe('connection_string');
    expect(authSelect().selectedOptions[0].textContent).toMatch(/connection string/i);
  });

  it('switching type keeps shared values and asks before dropping entered ones', async () => {
    mockFetch([listOf([])]);
    renderPage();
    await openRegister();
    await userEvent.selectOptions(authSelect(), 'basic');
    await userEvent.type(screen.getByLabelText(/username/i), 'alice');
    await userEvent.type(screen.getByLabelText(/^password/i), 'pw-1');

    // basic -> connection_string: username + password are shared, nothing dropped, no confirm.
    await userEvent.selectOptions(authSelect(), 'connection_string');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(authSelect().value).toBe('connection_string');
    expect(screen.getByLabelText(/^username/i)).toHaveValue('alice');
    expect(screen.getByLabelText(/^password/i)).toHaveValue('pw-1');

    // connection_string -> bearer would drop username/password: confirm first.
    await userEvent.selectOptions(authSelect(), 'bearer');
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent(/username/i);
    await userEvent.click(within(dialog).getByRole('button', { name: /cancel/i }));
    expect(authSelect().value).toBe('connection_string');
    expect(screen.getByLabelText(/^username/i)).toHaveValue('alice');

    await userEvent.selectOptions(authSelect(), 'bearer');
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /switch/i }));
    expect(authSelect().value).toBe('bearer');
    expect(screen.queryByLabelText(/^username/i)).not.toBeInTheDocument();
  });

  it('editing a connection_string connector shows its credential fields', async () => {
    mockFetch([
      listOf([{
        server_id: 'builtin-mongodb:orders-db', name: 'orders-db', builtin_type: 'builtin-mongodb', builtin_type_name: 'MongoDB',
        url: 'builtin://', auth_type: 'connection_string', has_builtin: true,
        auth_config: { uri: '<redacted>', username: 'alice', password: '<redacted>', database: 'orders' },
      }]),
    ]);
    renderPage();
    await screen.findByRole('link', { name: 'orders-db' });
    await userEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    expect(authSelect().value).toBe('connection_string');
    expect(screen.getByLabelText(/^username/i)).toHaveValue('alice');
    expect(screen.getByLabelText(/^database/i)).toHaveValue('orders');
    expect(screen.getByLabelText(/connection uri/i)).toHaveAttribute('type', 'password');
  });

  it('editing a connector of an unknown auth type keeps the type and shows its fields', async () => {
    mockFetch([
      listOf([{ server_id: 's-x', name: 'legacy', url: 'https://x.example.com', auth_type: 'weird_auth', auth_config: { magic: '<redacted>' } }]),
    ]);
    renderPage();
    await screen.findByRole('link', { name: 'legacy' });
    await userEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    expect(authSelect().value).toBe('weird_auth');
    expect(screen.getByLabelText(/magic/i)).toHaveAttribute('type', 'password');
  });
});

// ── A4: MongoDB fields + checkbox/textarea/file field types ──────────────────

/** GET /connectors/catalog `mongodb` entry as the backend serves it today. */
const MONGO_CATALOG_FIELDS = [
  { key: 'url', label: 'Connection URL', placeholder: 'service://host:port/db', field_type: 'url', required: true, hint: '' },
];

const mongoPrefill = {
  prefill: {
    connector_type: 'mongodb', type: 'builtin-mongodb', type_name: 'MongoDB', name: 'orders-db',
    url: 'mongodb://localhost:27017', auth_type: 'connection_string', auth_fields: MONGO_CATALOG_FIELDS,
  },
};

const postBody = (spy: ReturnType<typeof mockFetch>) => {
  const call = spy.mock.calls.find(([u, i]) => String(u).endsWith('/connectors') && (i as RequestInit)?.method === 'POST');
  return call ? JSON.parse(String((call[1] as RequestInit).body)) : undefined;
};

describe('A4 MongoDB connector fields', () => {
  it('renders every MongoDB option the handler reads', async () => {
    mockFetch([listOf([])]);
    renderPage(mongoPrefill);
    await screen.findByTestId('register-modal');
    expect(screen.getByLabelText(/connection uri/i)).toHaveAttribute('type', 'password');
    expect(screen.getByLabelText(/^username/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^password/i)).toHaveAttribute('type', 'password');
    expect(screen.getByLabelText(/auth source/i)).toBeInTheDocument();
    const mech = screen.getByLabelText(/auth mechanism/i);
    expect(within(mech).getByRole('option', { name: 'SCRAM-SHA-256' })).toBeInTheDocument();
    expect(screen.getByLabelText(/^database/i)).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /use tls/i })).not.toBeChecked();
    // TLS-only options appear once TLS is on.
    expect(screen.queryByLabelText(/ca certificate/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: /invalid certificates/i })).not.toBeInTheDocument();
  });

  it('TLS reveals the CA PEM (paste or file) and a warned allow-invalid-certs switch; all are submitted', async () => {
    const spy = mockFetch([
      listOf([]),
      { match: (u, i) => u.endsWith('/connectors') && i?.method === 'POST', response: { server_id: 'builtin-mongodb:orders-db', name: 'orders-db' } },
    ]);
    renderPage(mongoPrefill);
    await screen.findByTestId('register-modal');
    await userEvent.type(screen.getByLabelText(/connection uri/i), 'mongodb+srv://cluster0.example.net/');
    await userEvent.type(screen.getByLabelText(/^username/i), 'alice');
    await userEvent.type(screen.getByLabelText(/^password/i), 'S3cret');
    await userEvent.type(screen.getByLabelText(/auth source/i), 'admin');
    await userEvent.selectOptions(screen.getByLabelText(/auth mechanism/i), 'SCRAM-SHA-256');
    await userEvent.click(screen.getByRole('checkbox', { name: /use tls/i }));

    const pem = '-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n';
    await userEvent.upload(screen.getByLabelText(/upload ca certificate/i), new File([pem], 'ca.pem', { type: 'application/x-pem-file' }));
    await waitFor(() => expect(screen.getByLabelText(/^ca certificate/i)).toHaveValue(pem));

    expect(screen.queryByText(/disables server certificate verification/i)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('checkbox', { name: /invalid certificates/i }));
    expect(screen.getByText(/disables server certificate verification/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /^register$/i }));
    await waitFor(() => expect(postBody(spy)).toBeTruthy());
    expect(postBody(spy).auth_type).toBe('connection_string');
    expect(postBody(spy).auth_config).toMatchObject({
      uri: 'mongodb+srv://cluster0.example.net/', username: 'alice', password: 'S3cret', auth_source: 'admin',
      auth_mechanism: 'SCRAM-SHA-256', tls: 'true', tls_ca_pem: pem, tls_allow_invalid_certificates: 'true',
    });
    // The catalog's generic `url` field is the same URI — it is not collected twice.
    expect(postBody(spy).auth_config).not.toHaveProperty('url');
  });

  it('catalog-driven checkbox, textarea and file fields use the shared renderer', async () => {
    const spy = mockFetch([
      listOf([]),
      { match: (u, i) => u.endsWith('/connectors') && i?.method === 'POST', response: { server_id: 'x', name: 'svc' } },
    ]);
    renderPage({
      prefill: {
        connector_type: 'svc', name: 'svc', url: 'https://svc.example.com', auth_type: 'api_key',
        auth_fields: [
          { key: 'api_key', label: 'Key', placeholder: '', field_type: 'password', required: true, hint: '' },
          { key: 'verbose', label: 'Verbose', placeholder: '', field_type: 'checkbox', required: false, hint: '' },
          { key: 'notes', label: 'Notes', placeholder: '', field_type: 'textarea', required: false, hint: '' },
          { key: 'cert', label: 'Cert', placeholder: '', field_type: 'file', required: false, hint: '' },
        ],
      },
    });
    await screen.findByTestId('register-modal');
    await userEvent.type(screen.getByLabelText(/^key/i), 'k-1');
    await userEvent.click(screen.getByRole('checkbox', { name: /verbose/i }));
    await userEvent.type(screen.getByLabelText(/^notes/i), 'line1');
    expect(screen.getByLabelText(/^notes/i).tagName).toBe('TEXTAREA');
    await userEvent.upload(screen.getByLabelText(/upload cert/i), new File(['PEMDATA'], 'c.pem'));
    await waitFor(() => expect(screen.getByLabelText(/^cert/i)).toHaveValue('PEMDATA'));
    await userEvent.click(screen.getByRole('button', { name: /^register$/i }));
    await waitFor(() => expect(postBody(spy)).toBeTruthy());
    expect(postBody(spy).auth_config).toEqual({ api_key: 'k-1', verbose: 'true', notes: 'line1', cert: 'PEMDATA' });
  });
});
