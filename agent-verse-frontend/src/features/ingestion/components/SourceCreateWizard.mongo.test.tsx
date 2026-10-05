/**
 * SourceCreateWizard against the real ingestion API shapes (mongo re-audit
 * B1, B2): create errors inline, Test connection / preview before create.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeAll, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';

beforeAll(async () => {
  await import('./SourceCreateWizard');
}, 60_000);

type Route = { match: (url: string, init?: RequestInit) => boolean; status?: number; body: unknown };

function mockFetch(routes: Route[]) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    for (const r of routes) {
      if (r.match(url, init as RequestInit)) {
        return new Response(JSON.stringify(r.body), { status: r.status ?? 200, headers: { 'Content-Type': 'application/json' } });
      }
    }
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

const isPost = (path: RegExp) => (u: string, i?: RequestInit) => i?.method === 'POST' && path.test(u);

async function openMongoConfigure() {
  const { SourceCreateWizard } = await import('./SourceCreateWizard');
  const onClose = vi.fn();
  const onCreated = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <SourceCreateWizard onClose={onClose} onCreated={onCreated} />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByText('NoSQL Database'));
  await waitFor(() => expect(screen.getByText('mongodb')).toBeInTheDocument());
  await userEvent.click(screen.getByText('mongodb'));
  await waitFor(() => expect(screen.getByText('Source Name *')).toBeInTheDocument());
  await userEvent.type(screen.getByPlaceholderText('My mongodb source'), 'orders');
  await userEvent.type(screen.getByLabelText('Connection URI'), 'mongodb://db.example.com:27017/');
  return { onClose, onCreated };
}

const bodies = (spy: ReturnType<typeof mockFetch>, path: RegExp) =>
  spy.mock.calls
    .filter(([u, i]) => (i as RequestInit | undefined)?.method === 'POST' && path.test(String(u)))
    .map(([, i]) => JSON.parse(String((i as RequestInit).body ?? '{}')));

beforeEach(() => {
  vi.restoreAllMocks();
  useAuthStore.setState({ apiKey: 'av-test-key', tenantId: 'tenant-1', plan: 'free', isAuthenticated: true });
});

describe('B1 create errors are shown inline', () => {
  test('a 422 validation error is shown on the field it names; the dialog stays open', async () => {
    mockFetch([{
      match: isPost(/\/sources$/), status: 422,
      body: { detail: [
        { loc: ['body', 'name'], msg: 'String should have at most 200 characters', type: 'string_too_long' },
        { loc: ['body', 'connection_config', 'uri'], msg: 'Value error, invalid MongoDB URI', type: 'value_error' },
      ] },
    }]);
    const { onClose, onCreated } = await openMongoConfigure();
    await userEvent.click(screen.getByRole('button', { name: /create source/i }));
    expect(await screen.findByText('String should have at most 200 characters')).toBeInTheDocument();
    expect(screen.getByText(/invalid MongoDB URI/)).toBeInTheDocument();
    // per field: each message sits next to its input
    expect(screen.getByPlaceholderText('My mongodb source')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByLabelText('Connection URI')).toHaveAttribute('aria-invalid', 'true');
    expect(onClose).not.toHaveBeenCalled();
    expect(onCreated).not.toHaveBeenCalled();
  });

  test('an SSRF / egress refusal is shown as a clear reason without the host', async () => {
    mockFetch([{
      match: isPost(/\/sources$/), status: 422,
      body: { detail: 'Source destination refused by egress policy: db.internal.example.com resolves to 10.0.0.5 (private address)' },
    }]);
    await openMongoConfigure();
    await userEvent.click(screen.getByRole('button', { name: /create source/i }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/address is blocked/i);
    expect(alert).not.toHaveTextContent('10.0.0.5');
    expect(alert).not.toHaveTextContent('internal.example.com');
  });

  test('a quota refusal (429) is shown', async () => {
    mockFetch([{ match: isPost(/\/sources$/), status: 429, body: { detail: 'Source quota reached for plan free (3 sources)' } }]);
    await openMongoConfigure();
    await userEvent.click(screen.getByRole('button', { name: /create source/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/quota/i);
  });

  test('a successful create sends the config and reports the new source', async () => {
    const spy = mockFetch([{ match: isPost(/\/sources$/), status: 201, body: { source_id: 's1', name: 'orders' } }]);
    const { onCreated } = await openMongoConfigure();
    await userEvent.click(screen.getByRole('button', { name: /create source/i }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(bodies(spy, /\/sources$/)[0]).toMatchObject({ family: 'nosql_database', source_type: 'mongodb', connection_config: { uri: 'mongodb://db.example.com:27017/' } });
  });
});

describe('B2 Test connection before create, Preview after', () => {
  test('Test connection calls /sources/validate?check_connection=true with the unsaved config', async () => {
    const spy = mockFetch([{ match: isPost(/\/sources\/validate\?check_connection=true$/), body: { valid: true, errors: [], connection: { ok: true, latency_ms: 12, error: null } } }]);
    await openMongoConfigure();
    await userEvent.click(screen.getByRole('button', { name: /test connection/i }));
    expect(await screen.findByText(/connection ok/i)).toHaveTextContent(/12\s*ms/);
    expect(bodies(spy, /\/sources\/validate/)[0]).toMatchObject({ name: 'orders', family: 'nosql_database', source_type: 'mongodb', connection_config: { uri: 'mongodb://db.example.com:27017/' } });
    expect(bodies(spy, /\/sources$/)).toHaveLength(0); // nothing created
  });

  test('a failed check shows a friendly reason; raw text is sanitised', async () => {
    const raw = 'Connection check failed: db.example.com:27017: [Errno 61] Connection refused, Timeout: 5.0s, Topology Description: <...>';
    mockFetch([{ match: isPost(/\/sources\/validate/), body: { valid: false, errors: [raw], connection: { ok: false, latency_ms: null, error: raw.replace('Connection check failed: ', '') } } }]);
    await openMongoConfigure();
    await userEvent.click(screen.getByRole('button', { name: /test connection/i }));
    const result = await screen.findByTestId('connection-test-result');
    expect(result).toHaveTextContent(/refused the connection/i);
    await userEvent.click(within(result).getByRole('button', { name: /details/i }));
    expect(result).toHaveTextContent(/Errno 61/);
    expect(result).not.toHaveTextContent('db.example.com');
  });

  test('a validation 422 from the check is shown on its field', async () => {
    mockFetch([{ match: isPost(/\/sources\/validate/), status: 422, body: { detail: [{ loc: ['body', 'connection_config', 'uri'], msg: 'Value error, invalid MongoDB URI' }] } }]);
    await openMongoConfigure();
    await userEvent.click(screen.getByRole('button', { name: /test connection/i }));
    expect(await screen.findByText('invalid MongoDB URI')).toBeInTheDocument();
    expect(screen.getByLabelText('Connection URI')).toHaveAttribute('aria-invalid', 'true');
  });

  test('after create, Preview runs /sources/{id}/preview and shows the sample', async () => {
    const spy = mockFetch([
      { match: isPost(/\/sources$/), status: 201, body: { source_id: 'src-9', name: 'orders' } },
      { match: isPost(/\/sources\/src-9\/preview$/), body: { docs_previewed: 2, sample: [
        { doc_id: 'orders/1', status: 'would_index', chunks_would_create: 3, tokens_estimate: 120 },
        { doc_id: 'orders/2', status: 'would_index', chunks_would_create: 1, tokens_estimate: 40 },
      ] } },
    ]);
    const { onClose, onCreated } = await openMongoConfigure();
    await userEvent.click(screen.getByRole('button', { name: /create source/i }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(onClose).not.toHaveBeenCalled();
    await userEvent.click(await screen.findByRole('button', { name: /preview documents/i }));
    expect(await screen.findByText(/2 documents previewed/i)).toBeInTheDocument();
    expect(screen.getByText('orders/1')).toBeInTheDocument();
    expect(bodies(spy, /\/sources\/src-9\/preview$/)).toHaveLength(1);
    await userEvent.click(screen.getByRole('button', { name: /^done$/i }));
    expect(onClose).toHaveBeenCalled();
  });

  test('a preview that answers {error} is a failure, not an empty success', async () => {
    mockFetch([
      { match: isPost(/\/sources$/), status: 201, body: { source_id: 'src-9', name: 'orders' } },
      { match: isPost(/\/sources\/src-9\/preview$/), body: { error: 'Authentication failed.', docs_previewed: 0 } },
    ]);
    await openMongoConfigure();
    await userEvent.click(screen.getByRole('button', { name: /create source/i }));
    await userEvent.click(await screen.findByRole('button', { name: /preview documents/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/authentication failed/i);
    expect(screen.queryByText(/documents previewed/i)).not.toBeInTheDocument();
  });
});
