import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ModelRegistryPage } from './ModelRegistryPage';

const model = (over: Record<string, unknown>) => ({
  display_name: over.model_id,
  capabilities: ['text_generation'],
  cost_per_1k_input: 0,
  cost_per_1k_output: 0,
  supports_tools: true,
  supports_vision: false,
  supports_structured_output: false,
  quality_score: 0.5,
  is_available: true,
  provider_ready: true,
  source: 'override',
  base_url: null,
  ...over,
  key: `${over.provider}/${over.model_id}`,
});

const REGISTRY = {
  total: 3,
  capabilities: [
    {
      capability: 'text_generation',
      selected_model_id: 'cheap-llm',
      fallback_model_ids: ['pricey-llm'],
      order_mode: 'cost',
      preference: [],
      models: [
        model({ provider: 'custom', model_id: 'cheap-llm', rank: 1, base_url: 'http://192.168.63.104:30080/v1', supports_structured_output: true, quality_score: 0.7, cost_per_1k_output: 0.0004 }),
        model({ provider: 'nvidia', model_id: 'pricey-llm', cost_per_1k_input: 0.02, rank: 2, source: 'env' }),
        model({ provider: 'groq', model_id: 'keyless-llm', cost_per_1k_input: 0.03, rank: 3, provider_ready: false }),
      ],
    },
    {
      capability: 'embedding',
      selected_model_id: 'embed-a',
      fallback_model_ids: [],
      order_mode: 'preference',
      preference: ['voyage/embed-a'],
      note: 'No runtime failover between embedding models.',
      models: [model({ provider: 'voyage', model_id: 'embed-a', capabilities: ['embedding'], rank: 1 })],
    },
  ],
};

const CATALOG = {
  providers: [
    {
      provider: 'groq', label: 'Groq', ready: true, env_hint: 'GROQ_API_KEY',
      models: [
        { model_id: 'llama-3.3-70b', display_name: 'Llama 3.3 70B', capabilities: ['text_generation'], cost_per_1k_input: 0.0006, cost_per_1k_output: 0.0008, supports_tools: true, supports_vision: false, quality_score: 0.8, already_configured: false },
      ],
    },
    {
      provider: 'xai', label: 'xAI', ready: false, env_hint: 'XAI_API_KEY',
      models: [
        { model_id: 'grok-4', display_name: 'Grok 4', capabilities: ['text_generation'], cost_per_1k_input: 0.003, cost_per_1k_output: 0.015, supports_tools: true, supports_vision: true, quality_score: 0.9, already_configured: false },
        { model_id: 'grok-mini', display_name: 'Grok mini', capabilities: ['text_generation'], cost_per_1k_input: 0.0003, cost_per_1k_output: 0.0005, supports_tools: true, supports_vision: false, quality_score: 0.7, already_configured: true, base_url: 'http://10.0.0.5:8000/v1' },
      ],
    },
  ],
};

type Access = { can_modify: boolean; via: 'admin_key' | 'tenant_admin' | null; needs_admin_key: boolean; reason: string };

const NEEDS_KEY: Access = {
  can_modify: false, via: null, needs_admin_key: true,
  reason: 'Platform admin privileges required to modify the model registry.',
};
const TENANT_ADMIN: Access = { can_modify: true, via: 'tenant_admin', needs_admin_key: false, reason: '' };

const TEST_OK = {
  ok: true, latency_ms: 42.4, probe: 'chat', model_listed: true,
  served_models: ['gpt-oss-20b'], detail: 'Chat completion returned 1 choice.', error: null,
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const headersOf = (init?: RequestInit) => (init?.headers ?? {}) as Record<string, string>;

/**
 * Default: the caller is not a tenant admin, so the backend wants the platform
 * admin key; typing 'admin-secret' unlocks modification via admin_key.
 */
function mockFetch(opts: {
  access?: Access; registry?: unknown; postStatus?: number; postBody?: unknown;
  testStatus?: number; testBody?: unknown;
} = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/models/configured/access')) {
      if (opts.access) return json(opts.access);
      return headersOf(init)['X-Admin-Key'] === 'admin-secret'
        ? json({ can_modify: true, via: 'admin_key', needs_admin_key: true, reason: '' })
        : json(NEEDS_KEY);
    }
    if (url.includes('/models/configured/reseed')) return json({ status: 'ok', configured_models: 3 });
    if (url.includes('/models/configured/test-endpoint'))
      return json(opts.testBody ?? TEST_OK, opts.testStatus ?? 200);
    if (url.includes('/models/catalog/import')) return json({ status: 'imported', imported: 1, skipped: 0 });
    if (url.includes('/models/catalog')) return json(CATALOG);
    if (url.includes('/models/preferences/') && method === 'PUT')
      return json({ status: 'saved', capability: 'text_generation', order: [] });
    if (url.includes('/models/preferences/') && method === 'DELETE')
      return json({ status: 'reset', capability: 'text_generation' });
    if (url.includes('/models/configured') && method === 'POST')
      return json(opts.postBody ?? { status: 'ok', model_id: 'gpt-oss-20b' }, opts.postStatus ?? 200);
    if (url.includes('/models/configured') && method === 'DELETE') return json({ status: 'deleted' });
    if (url.includes('/models/configured')) return json(opts.registry ?? REGISTRY);
    return json({});
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><ModelRegistryPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

async function unlockWithAdminKey() {
  await userEvent.type(await screen.findByPlaceholderText(/Platform admin key/i), 'admin-secret');
  await waitFor(() => expect(screen.getByRole('button', { name: /Add Model/i })).toBeEnabled());
}

const findCall = (spy: ReturnType<typeof mockFetch>, pred: (url: string, init: RequestInit) => boolean) =>
  spy.mock.calls.find(([u, i]) => pred(String(u), (i ?? {}) as RequestInit));

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ModelRegistryPage', () => {
  test('renders models in effective order with primary / fallback / env badges', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByRole('heading', { name: /Model Registry/i })).toBeInTheDocument();
    expect(await screen.findByText('cheap-llm')).toBeInTheDocument();
    const reasoning = screen.getByRole('heading', { name: 'Reasoning' }).closest('section') as HTMLElement;
    const rows = within(reasoning).getAllByRole('listitem');
    expect(rows.map((r) => within(r).getByText(/-llm$/).textContent)).toEqual(['cheap-llm', 'pricey-llm', 'keyless-llm']);
    expect(within(rows[0]).getByText('Primary')).toBeInTheDocument();
    expect(within(rows[1]).getByText('Fallback 1')).toBeInTheDocument();
    expect(within(rows[1]).getByText('env')).toBeInTheDocument();
    expect(within(reasoning).getByText('Cheapest first')).toBeInTheDocument();
  });

  test('provider_ready=false shows a "No API key" badge and dims the row', async () => {
    mockFetch();
    renderPage();
    const name = await screen.findByText('keyless-llm');
    const row = name.closest('li') as HTMLElement;
    expect(within(row).getByText('No API key')).toBeInTheDocument();
    expect(row.className).toMatch(/opacity-60/);
    expect(within(row).queryByText(/Fallback/)).not.toBeInTheDocument();
    expect(screen.getByText(/skipped at runtime until their provider key is set/i)).toBeInTheDocument();
  });

  test('shows the group note and preference mode', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByText(/No runtime failover between embedding models/i)).toBeInTheDocument();
    expect(screen.getByText('Preference order')).toBeInTheDocument();
  });

  test('needs_admin_key: key input is shown, actions disabled with the reason, enabled once the key is accepted', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('cheap-llm');
    const addBtn = screen.getByRole('button', { name: /Add Model/i });
    const reseedBtn = screen.getByRole('button', { name: /Reseed from config/i });
    const importBtn = screen.getByRole('button', { name: /Import catalog/i });
    expect(await screen.findByPlaceholderText(/Platform admin key/i)).toBeInTheDocument();
    expect(addBtn).toBeDisabled();
    expect(reseedBtn).toBeDisabled();
    expect(importBtn).toBeDisabled();
    expect(screen.getByText(/Platform admin privileges required/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Move cheap-llm down/i })).not.toBeInTheDocument();

    await unlockWithAdminKey();
    expect(reseedBtn).toBeEnabled();
    expect(importBtn).toBeEnabled();
    // The access probe was re-run with the typed key.
    expect(
      findCall(spy, (u, i) => u.includes('/models/configured/access') && headersOf(i)['X-Admin-Key'] === 'admin-secret'),
    ).toBeDefined();
    // The key input stays visible once the operator has typed into it.
    expect(screen.getByPlaceholderText(/Platform admin key/i)).toBeInTheDocument();
  });

  test('tenant_admin can modify without an admin key and sees no key input', async () => {
    const spy = mockFetch({ access: TENANT_ADMIN });
    renderPage();
    await screen.findByText('cheap-llm');
    expect(await screen.findByText(/Signed in as platform admin/i)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/Platform admin key/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Add Model/i })).toBeEnabled();

    await userEvent.click(screen.getByRole('button', { name: /Reseed from config/i }));
    await waitFor(() => expect(findCall(spy, (u) => u.includes('/models/configured/reseed'))).toBeDefined());
    const [, init] = findCall(spy, (u) => u.includes('/models/configured/reseed'))!;
    expect(headersOf(init as RequestInit)['X-Admin-Key']).toBeUndefined();
  });

  test('reorder then Save order PUTs the full reordered key list', async () => {
    const spy = mockFetch({ access: TENANT_ADMIN });
    renderPage();
    await screen.findByText('cheap-llm');
    const save = await screen.findByRole('button', { name: /Save Reasoning order/i });
    expect(save).toBeDisabled();

    await userEvent.click(screen.getByRole('button', { name: /Move pricey-llm up/i }));
    expect(screen.getByText(/Unsaved order/i)).toBeInTheDocument();
    // Local preview: pricey-llm is now first and primary.
    const row = screen.getByText('pricey-llm').closest('li') as HTMLElement;
    expect(within(row).getByText('Primary')).toBeInTheDocument();

    expect(save).toBeEnabled();
    await userEvent.click(save);
    await waitFor(() =>
      expect(findCall(spy, (u, i) => u.includes('/models/preferences/text_generation') && i.method === 'PUT')).toBeDefined(),
    );
    const [, init] = findCall(spy, (u, i) => u.includes('/models/preferences/text_generation') && i.method === 'PUT')!;
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      order: ['nvidia/pricey-llm', 'custom/cheap-llm', 'groq/keyless-llm'],
    });
  });

  test('Reset to cost order sends a DELETE for the capability preference', async () => {
    const spy = mockFetch({ access: TENANT_ADMIN });
    renderPage();
    await screen.findByText('embed-a');
    await userEvent.click(screen.getByRole('button', { name: /Reset Embeddings to cost order/i }));
    await waitFor(() =>
      expect(findCall(spy, (u, i) => u.includes('/models/preferences/embedding') && i.method === 'DELETE')).toBeDefined(),
    );
  });

  test('catalog import posts the selected providers', async () => {
    const spy = mockFetch({ access: TENANT_ADMIN });
    renderPage();
    await screen.findByText('cheap-llm');
    await userEvent.click(screen.getByRole('button', { name: /Import catalog/i }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('Groq')).toBeInTheDocument();
    expect(within(dialog).getByText(/No key — set XAI_API_KEY/)).toBeInTheDocument();
    expect(within(dialog).getAllByText('Ready')).toHaveLength(1);

    const importSelected = within(dialog).getByRole('button', { name: /Import selected/i });
    expect(importSelected).toBeDisabled();
    await userEvent.click(within(dialog).getByRole('checkbox', { name: /Select Groq/i }));
    await userEvent.click(importSelected);
    await waitFor(() =>
      expect(findCall(spy, (u, i) => u.includes('/models/catalog/import') && i.method === 'POST')).toBeDefined(),
    );
    const [, init] = findCall(spy, (u, i) => u.includes('/models/catalog/import') && i.method === 'POST')!;
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ providers: ['groq'] });
    expect(await within(dialog).findByRole('status')).toHaveTextContent(/Imported 1 model, skipped 0/);
  });

  test('catalog: partial per-model selection posts model_ids; Import all posts an empty body', async () => {
    const spy = mockFetch({ access: TENANT_ADMIN });
    renderPage();
    await screen.findByText('cheap-llm');
    await userEvent.click(screen.getByRole('button', { name: /Import catalog/i }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(await within(dialog).findByRole('button', { name: /Show xAI models/i }));
    await userEvent.click(within(dialog).getByRole('checkbox', { name: /Select grok-4/i }));
    await userEvent.click(within(dialog).getByRole('button', { name: /Import selected/i }));
    await waitFor(() =>
      expect(findCall(spy, (u, i) => u.includes('/models/catalog/import') && i.method === 'POST')).toBeDefined(),
    );
    const imports = () => spy.mock.calls.filter(([u, i]) => String(u).includes('/models/catalog/import') && (i as RequestInit)?.method === 'POST');
    expect(JSON.parse((imports()[0][1] as RequestInit).body as string)).toEqual({ model_ids: ['xai/grok-4'] });

    await userEvent.click(within(dialog).getByRole('button', { name: /Import all/i }));
    await waitFor(() => expect(imports()).toHaveLength(2));
    expect(JSON.parse((imports()[1][1] as RequestInit).body as string)).toEqual({});
  });

  test('add-model modal validates required fields then POSTs with the admin header', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('cheap-llm');
    await unlockWithAdminKey();
    await userEvent.click(screen.getByRole('button', { name: /Add Model/i }));

    // Save with empty model id → inline validation error, no POST yet.
    await userEvent.click(screen.getByRole('button', { name: /^Save$/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/Model ID: Model ID is required/i);
    expect(screen.getByLabelText(/Model ID/i)).toHaveAttribute('aria-invalid', 'true');
    expect(spy.mock.calls.some(([, i]) => (i as RequestInit)?.method === 'POST')).toBe(false);

    await userEvent.type(screen.getByPlaceholderText(/openai\/gpt-oss-20b/i), 'gpt-oss-20b');
    await userEvent.click(screen.getByRole('button', { name: /^Save$/i }));
    await waitFor(() =>
      expect(
        findCall(spy, (u, i) => u.endsWith('/models/configured') && i.method === 'POST' && headersOf(i)['X-Admin-Key'] === 'admin-secret'),
      ).toBeDefined(),
    );
    const [, init] = findCall(spy, (u, i) => u.endsWith('/models/configured') && i.method === 'POST')!;
    expect(JSON.parse((init as RequestInit).body as string).provider).toBe('nvidia');
  });

  test('remove sends a DELETE for the chosen model', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('pricey-llm');
    await unlockWithAdminKey();
    await userEvent.click(screen.getByRole('button', { name: /Remove pricey-llm/i }));
    // Nothing is deleted until the confirmation is accepted.
    const confirm = await screen.findByRole('alertdialog', { name: /Remove pricey-llm\?/i });
    expect(spy.mock.calls.some(([, i]) => (i as RequestInit)?.method === 'DELETE')).toBe(false);
    await userEvent.click(within(confirm).getByRole('button', { name: /^Remove$/ }));
    await waitFor(() =>
      expect(findCall(spy, (u, i) => u.includes('/models/configured/nvidia/pricey-llm') && i.method === 'DELETE')).toBeDefined(),
    );
  });

  test('reseed triggers the reseed endpoint with the admin key', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('cheap-llm');
    await unlockWithAdminKey();
    await userEvent.click(screen.getByRole('button', { name: /Reseed from config/i }));
    await waitFor(() =>
      expect(findCall(spy, (u, i) => u.includes('/models/configured/reseed') && headersOf(i)['X-Admin-Key'] === 'admin-secret')).toBeDefined(),
    );
  });

  test('shows an error state when the registry request fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('nope', { status: 500, headers: { 'Content-Type': 'text/plain' } }),
    );
    renderPage();
    expect(await screen.findByText(/Failed to load the model registry/i)).toBeInTheDocument();
    // Nothing is known, so no "No model yet" empty states are claimed; Retry is offered.
    expect(screen.queryByText(/yet — add one/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  test('empty capability groups render a per-capability empty state', async () => {
    mockFetch({ registry: { total: 0, capabilities: [] } });
    renderPage();
    await waitFor(() =>
      expect(screen.getAllByText(/yet — add one/i)).toHaveLength(5),
    );
    expect(screen.getByTestId('empty-embedding')).toHaveTextContent('No embedding model yet — add one');
    expect(screen.getByTestId('empty-ocr')).toHaveTextContent('No OCR model yet — add one');
    expect(screen.getByTestId('empty-rerank')).toHaveTextContent('No reranker yet — add one');
  });

  test('does not fetch the registry when no api key is present', async () => {
    useAuthStore.setState({ apiKey: '', tenantId: 't', plan: 'free', isAuthenticated: false });
    const spy = mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getAllByText(/yet — add one/i)).toHaveLength(5),
    );
    expect(spy.mock.calls.some(([u]) => String(u).includes('/models/configured'))).toBe(false);
  });

  test('toggling a capability chip removes it, then toggling another adds it', async () => {
    mockFetch({ access: TENANT_ADMIN });
    renderPage();
    await screen.findByText('cheap-llm');
    await userEvent.click(await screen.findByRole('button', { name: /Add Model/i }));

    const reasoningChip = screen.getByRole('button', { name: 'Reasoning' });
    expect(reasoningChip.className).toMatch(/bg-primary/);
    await userEvent.click(reasoningChip);
    expect(reasoningChip.className).not.toMatch(/bg-primary/);

    const visionChip = screen.getByRole('button', { name: 'Vision' });
    expect(visionChip.className).not.toMatch(/bg-primary/);
    await userEvent.click(visionChip);
    expect(visionChip.className).toMatch(/bg-primary/);
  });

  test('editing provider, costs, quality and the tools/vision checkboxes updates the POST body', async () => {
    const spy = mockFetch({ access: TENANT_ADMIN });
    renderPage();
    await screen.findByText('cheap-llm');
    await userEvent.click(await screen.findByRole('button', { name: /Add Model/i }));

    await userEvent.type(screen.getByPlaceholderText(/openai\/gpt-oss-20b/i), 'gpt-oss-20b');
    const provider = screen.getByLabelText('Provider');
    expect(provider).toHaveValue('nvidia');
    await userEvent.selectOptions(provider, 'groq');
    const costIn = screen.getByLabelText(/Cost \/ 1k input/i);
    await userEvent.clear(costIn);
    await userEvent.type(costIn, '0.0012');
    const costOut = screen.getByLabelText(/Cost \/ 1k output/i);
    await userEvent.clear(costOut);
    await userEvent.type(costOut, '0.0034');
    const quality = screen.getByLabelText(/Quality/i);
    await userEvent.clear(quality);
    await userEvent.type(quality, '0.9');

    const toolsCheckbox = screen.getByRole('checkbox', { name: /Supports tools/i });
    const visionCheckbox = screen.getByRole('checkbox', { name: /Supports vision/i });
    expect(toolsCheckbox).toBeChecked();
    expect(visionCheckbox).not.toBeChecked();
    await userEvent.click(toolsCheckbox);
    await userEvent.click(visionCheckbox);

    await userEvent.click(screen.getByRole('button', { name: /^Save$/i }));
    await waitFor(() =>
      expect(findCall(spy, (u, i) => u.endsWith('/models/configured') && i.method === 'POST')).toBeDefined(),
    );
    const [, init] = findCall(spy, (u, i) => u.endsWith('/models/configured') && i.method === 'POST')!;
    const posted = JSON.parse((init as RequestInit).body as string);
    expect(posted.provider).toBe('groq');
    expect(posted.cost_per_1k_input).toBeCloseTo(0.0012);
    expect(posted.cost_per_1k_output).toBeCloseTo(0.0034);
    expect(posted.quality_score).toBeCloseTo(0.9);
    expect(posted.supports_tools).toBe(false);
    expect(posted.supports_vision).toBe(true);
  });

  test('selecting the vision capability forces supports_vision on even if unchecked', async () => {
    const spy = mockFetch({ access: TENANT_ADMIN });
    renderPage();
    await screen.findByText('cheap-llm');
    await userEvent.click(await screen.findByRole('button', { name: /Add Model/i }));

    await userEvent.type(screen.getByPlaceholderText(/openai\/gpt-oss-20b/i), 'vision-model');
    await userEvent.click(screen.getByRole('button', { name: 'Vision' }));
    expect(screen.getByRole('checkbox', { name: /Supports vision/i })).not.toBeChecked();

    await userEvent.click(screen.getByRole('button', { name: /^Save$/i }));
    await waitFor(() =>
      expect(findCall(spy, (u, i) => u.endsWith('/models/configured') && i.method === 'POST')).toBeDefined(),
    );
    const [, init] = findCall(spy, (u, i) => u.endsWith('/models/configured') && i.method === 'POST')!;
    const posted = JSON.parse((init as RequestInit).body as string);
    expect(posted.capabilities).toEqual(expect.arrayContaining(['text_generation', 'vision']));
    expect(posted.supports_vision).toBe(true);
  });

  test('cancel closes the modal without posting anything', async () => {
    const spy = mockFetch({ access: TENANT_ADMIN });
    renderPage();
    await screen.findByText('cheap-llm');
    await userEvent.click(await screen.findByRole('button', { name: /Add Model/i }));
    expect(screen.getByText(/Add \/ override a model/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /^Cancel$/i }));
    expect(screen.queryByText(/Add \/ override a model/i)).not.toBeInTheDocument();
    expect(spy.mock.calls.some(([, i]) => (i as RequestInit)?.method === 'POST')).toBe(false);
  });

  test('shows the server error message when saving a model fails', async () => {
    mockFetch({
      access: TENANT_ADMIN,
      postStatus: 400,
      postBody: { error: { message: 'model_id already registered' } },
    });
    renderPage();
    await screen.findByText('cheap-llm');
    await userEvent.click(await screen.findByRole('button', { name: /Add Model/i }));
    await userEvent.type(screen.getByPlaceholderText(/openai\/gpt-oss-20b/i), 'dup-model');
    await userEvent.click(screen.getByRole('button', { name: /^Save$/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/model_id already registered/i);
    expect(screen.getByText(/Add \/ override a model/i)).toBeInTheDocument();
  });

  test('falls back to admin-key gating when the access probe fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/models/configured/access')) return json({ detail: 'Not Found' }, 404);
      if (url.includes('/models/configured')) return json(REGISTRY);
      return json({});
    });
    renderPage();
    await screen.findByText('cheap-llm');
    const addBtn = screen.getByRole('button', { name: /Add Model/i });
    expect(addBtn).toBeDisabled();
    await userEvent.type(await screen.findByPlaceholderText(/Platform admin key/i), 'k2');
    await waitFor(() => expect(addBtn).toBeEnabled());
  });
});

describe('ModelRegistryPage — model endpoints (base_url)', () => {
  const ENDPOINT = 'http://192.168.63.104:30080/v1';
  const postedModels = (spy: ReturnType<typeof mockFetch>) =>
    spy.mock.calls
      .filter(([u, i]) => String(u).endsWith('/models/configured') && (i as RequestInit)?.method === 'POST')
      .map(([, i]) => JSON.parse((i as RequestInit).body as string));
  const testCalls = (spy: ReturnType<typeof mockFetch>) =>
    spy.mock.calls.filter(([u]) => String(u).includes('/models/configured/test-endpoint'));

  async function openAddDialog() {
    await screen.findByText('cheap-llm');
    await userEvent.click(await screen.findByRole('button', { name: /Add Model/i }));
    return screen.getByRole('dialog');
  }

  test('shows the endpoint host on rows with a base_url, full URL in the title', async () => {
    mockFetch();
    renderPage();
    const row = (await screen.findByText('cheap-llm')).closest('li') as HTMLElement;
    const endpoint = within(row).getByText('192.168.63.104:30080');
    expect(endpoint.closest('[title]')).toHaveAttribute('title', ENDPOINT);
    const other = screen.getByText('pricey-llm').closest('li') as HTMLElement;
    expect(within(other).queryByLabelText(/^Endpoint /)).not.toBeInTheDocument();
  });

  test('base_url is posted (trimmed) when filled and omitted when empty', async () => {
    const spy = mockFetch({ access: TENANT_ADMIN });
    renderPage();
    const dialog = await openAddDialog();
    expect(within(dialog).getByText(/Base URL of an OpenAI-compatible server/i)).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText(/Model ID/i), 'plain-model');
    await userEvent.click(within(dialog).getByRole('button', { name: /^Save$/i }));
    await waitFor(() => expect(postedModels(spy)).toHaveLength(1));
    expect(postedModels(spy)[0]).not.toHaveProperty('base_url');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    const dialog2 = await openAddDialog();
    await userEvent.type(within(dialog2).getByLabelText(/Model ID/i), 'vllm-model');
    await userEvent.type(within(dialog2).getByLabelText(/Endpoint URL/i), `  ${ENDPOINT}  `);
    await userEvent.click(within(dialog2).getByRole('button', { name: /^Save$/i }));
    await waitFor(() => expect(postedModels(spy)).toHaveLength(2));
    expect(postedModels(spy)[1]).toMatchObject({ model_id: 'vllm-model', base_url: ENDPOINT });
  });

  test('a 400 detail from save (refused URL) is shown in the dialog', async () => {
    mockFetch({
      access: TENANT_ADMIN,
      postStatus: 400,
      postBody: { detail: 'base_url host 10.1.2.3 is not in MODEL_ENDPOINT_ALLOWLIST' },
    });
    renderPage();
    const dialog = await openAddDialog();
    await userEvent.type(within(dialog).getByLabelText(/Model ID/i), 'm');
    await userEvent.type(within(dialog).getByLabelText(/Endpoint URL/i), 'http://10.1.2.3/v1');
    await userEvent.click(within(dialog).getByRole('button', { name: /^Save$/i }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(/not in MODEL_ENDPOINT_ALLOWLIST/);
  });

  test('Test connection is gated on model id + URL, posts the form fields with the admin key, and shows success', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('cheap-llm');
    await unlockWithAdminKey();
    await userEvent.click(screen.getByRole('button', { name: /Add Model/i }));
    const dialog = screen.getByRole('dialog');
    const testBtn = within(dialog).getByRole('button', { name: /Test connection/i });
    expect(testBtn).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText(/Model ID/i), 'gpt-oss-20b');
    expect(testBtn).toBeDisabled();
    await userEvent.selectOptions(within(dialog).getByLabelText('Provider'), 'onprem');
    await userEvent.type(within(dialog).getByLabelText(/Endpoint URL/i), ENDPOINT);
    await userEvent.click(within(dialog).getByRole('button', { name: 'Embeddings' }));
    expect(testBtn).toBeEnabled();
    await userEvent.click(testBtn);

    const result = await within(dialog).findByTestId('endpoint-test-result');
    expect(result).toHaveTextContent('Connected · 42 ms · chat');
    expect(result).toHaveTextContent('Chat completion returned 1 choice.');
    expect(result).not.toHaveTextContent(/does not list/);
    const [, init] = testCalls(spy)[0];
    expect((init as RequestInit).method).toBe('POST');
    expect(headersOf(init as RequestInit)['X-Admin-Key']).toBe('admin-secret');
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      provider: 'onprem', model_id: 'gpt-oss-20b', base_url: ENDPOINT,
      capabilities: ['text_generation', 'embedding'], output_dimensions: null, thinking: 'auto',
    });

    // Editing the URL invalidates the shown result.
    await userEvent.type(within(dialog).getByLabelText(/Endpoint URL/i), 'x');
    expect(within(dialog).queryByTestId('endpoint-test-result')).not.toBeInTheDocument();
  });

  test('Test connection warns when the server does not list the model', async () => {
    mockFetch({
      access: TENANT_ADMIN,
      testBody: { ...TEST_OK, model_listed: false, served_models: ['llama-3-8b', 'qwen2-7b'] },
    });
    renderPage();
    const dialog = await openAddDialog();
    await userEvent.type(within(dialog).getByLabelText(/Model ID/i), 'gpt-oss-20b');
    await userEvent.type(within(dialog).getByLabelText(/Endpoint URL/i), ENDPOINT);
    await userEvent.click(within(dialog).getByRole('button', { name: /Test connection/i }));
    const result = await within(dialog).findByTestId('endpoint-test-result');
    expect(result).toHaveTextContent('Connected');
    expect(result).toHaveTextContent(
      'The server does not list gpt-oss-20b; it serves: llama-3-8b, qwen2-7b',
    );
  });

  test('Test connection shows the error for ok:false and the detail for a refused URL (400)', async () => {
    mockFetch({
      access: TENANT_ADMIN,
      testBody: { ...TEST_OK, ok: false, error: 'Connection refused', detail: '' },
    });
    renderPage();
    const dialog = await openAddDialog();
    await userEvent.type(within(dialog).getByLabelText(/Model ID/i), 'gpt-oss-20b');
    await userEvent.type(within(dialog).getByLabelText(/Endpoint URL/i), ENDPOINT);
    await userEvent.click(within(dialog).getByRole('button', { name: /Test connection/i }));
    const failed = await within(dialog).findByTestId('endpoint-test-result');
    expect(failed).toHaveAttribute('role', 'alert');
    expect(failed).toHaveTextContent('Connection refused');
    expect(failed).not.toHaveTextContent('Connected ·');
  });

  test('Test connection shows the 400 detail when the URL is refused', async () => {
    mockFetch({
      access: TENANT_ADMIN,
      testStatus: 400,
      testBody: { detail: 'Private host 192.168.63.104 is not allowed' },
    });
    renderPage();
    const dialog = await openAddDialog();
    await userEvent.type(within(dialog).getByLabelText(/Model ID/i), 'gpt-oss-20b');
    await userEvent.type(within(dialog).getByLabelText(/Endpoint URL/i), ENDPOINT);
    await userEvent.click(within(dialog).getByRole('button', { name: /Test connection/i }));
    expect(await within(dialog).findByTestId('endpoint-test-result')).toHaveTextContent(
      'Private host 192.168.63.104 is not allowed',
    );
  });

  test('Edit is hidden without modify rights', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('cheap-llm');
    expect(screen.queryByRole('button', { name: 'Edit cheap-llm' })).not.toBeInTheDocument();
  });

  test('Edit pre-fills the dialog from the row and saving upserts it', async () => {
    const spy = mockFetch({ access: TENANT_ADMIN });
    renderPage();
    await screen.findByText('cheap-llm');
    await userEvent.click(screen.getByRole('button', { name: 'Edit cheap-llm' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('heading', { name: 'Edit model' })).toBeInTheDocument();
    expect(within(dialog).getByText(/changing either creates a new entry/i)).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/Model ID/i)).toHaveValue('cheap-llm');
    expect(within(dialog).getByLabelText('Provider')).toHaveValue('custom');
    expect(within(dialog).getByLabelText(/Endpoint URL/i)).toHaveValue(ENDPOINT);
    expect(within(dialog).getByLabelText(/Cost \/ 1k output/i)).toHaveValue(0.0004);
    expect(within(dialog).getByLabelText(/Quality/i)).toHaveValue(0.7);
    expect(within(dialog).getByRole('checkbox', { name: /Supports tools/i })).toBeChecked();
    expect(within(dialog).getByRole('button', { name: 'Reasoning' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(dialog).getByRole('button', { name: 'Vision' })).toHaveAttribute('aria-pressed', 'false');

    const quality = within(dialog).getByLabelText(/Quality/i);
    await userEvent.clear(quality);
    await userEvent.type(quality, '0.8');
    await userEvent.click(within(dialog).getByRole('button', { name: /^Save$/i }));
    await waitFor(() => expect(postedModels(spy)).toHaveLength(1));
    expect(postedModels(spy)[0]).toMatchObject({
      model_id: 'cheap-llm', provider: 'custom', base_url: ENDPOINT,
      capabilities: ['text_generation'], cost_per_1k_output: 0.0004, quality_score: 0.8,
      supports_tools: true, supports_structured_output: true,
    });
    expect(postedModels(spy)[0]).not.toHaveProperty('display_name');
  });

  test('catalog import dialog shows a catalog model base_url', async () => {
    mockFetch({ access: TENANT_ADMIN });
    renderPage();
    await screen.findByText('cheap-llm');
    await userEvent.click(screen.getByRole('button', { name: /Import catalog/i }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(await within(dialog).findByRole('button', { name: /Show xAI models/i }));
    expect(within(dialog).getByText('http://10.0.0.5:8000/v1')).toHaveAttribute('title', 'http://10.0.0.5:8000/v1');
  });
});

describe('ModelRegistryPage — per-model API key and embedding dimensions', () => {
  const GEMINI_BASE = 'https://generativelanguage.googleapis.com/v1beta/openai';
  const GEMINI_ROW = model({
    provider: 'gemini', model_id: 'gemini-embedding-001', capabilities: ['embedding'], rank: 1,
    base_url: GEMINI_BASE, has_api_key: true, output_dimensions: 1536,
    dimensions: 1536, index_dimension: 1536, dimension_mismatch: false,
  });
  const PROVIDER_KEY_ROW = model({
    provider: 'voyage', model_id: 'voyage-3.5', capabilities: ['embedding'], rank: 2,
    has_api_key: false, index_dimension: 1536,
  });
  const KEY_REGISTRY = {
    total: 2,
    capabilities: [{
      capability: 'embedding', selected_model_id: 'gemini-embedding-001', fallback_model_ids: [],
      order_mode: 'preference', preference: ['gemini/gemini-embedding-001'],
      models: [GEMINI_ROW, PROVIDER_KEY_ROW],
    }],
  };
  const EMBED_OK = {
    ok: true, latency_ms: 120, probe: 'embedding', model_listed: true, served_models: [],
    detail: '1536-dimension embedding', error: null, dimensions: 1536, index_dimension: 1536,
    dimension_mismatch: false, requested_dimensions: 1536, dimensions_ignored: false,
  };
  const postedModels = (spy: ReturnType<typeof mockFetch>) =>
    spy.mock.calls
      .filter(([u, i]) => String(u).endsWith('/models/configured') && (i as RequestInit)?.method === 'POST')
      .map(([, i]) => JSON.parse((i as RequestInit).body as string));
  const testBodies = (spy: ReturnType<typeof mockFetch>) =>
    spy.mock.calls
      .filter(([u]) => String(u).includes('/models/configured/test-endpoint'))
      .map(([, i]) => JSON.parse((i as RequestInit).body as string));

  async function openEdit(id = 'gemini-embedding-001') {
    await screen.findByText(id);
    await userEvent.click(screen.getByRole('button', { name: `Edit ${id}` }));
    return screen.getByRole('dialog');
  }

  async function openAdd() {
    await screen.findByText('gemini-embedding-001');
    await userEvent.click(screen.getByRole('button', { name: /Add Model/i }));
    return screen.getByRole('dialog');
  }

  test('rows show "Key saved" for a model with its own key and "Provider key" otherwise', async () => {
    mockFetch({ access: TENANT_ADMIN, registry: KEY_REGISTRY });
    renderPage();
    const gemini = (await screen.findByText('gemini-embedding-001')).closest('li') as HTMLElement;
    expect(within(gemini).getByText('Key saved')).toBeInTheDocument();
    expect(within(gemini).queryByText('Provider key')).not.toBeInTheDocument();
    expect(within(gemini).getByText(/1536-d requested/)).toBeInTheDocument();
    const voyage = screen.getByText('voyage-3.5').closest('li') as HTMLElement;
    expect(within(voyage).getByText('Provider key')).toBeInTheDocument();
    expect(within(voyage).queryByText('Key saved')).not.toBeInTheDocument();
  });

  test('the typed key is sent on Test connection and on Save, never stored, and cleared after save', async () => {
    const spy = mockFetch({ access: TENANT_ADMIN, registry: KEY_REGISTRY, testBody: EMBED_OK });
    renderPage();
    const dialog = await openEdit('voyage-3.5');
    const keyInput = within(dialog).getByLabelText('API key');
    expect(keyInput).toHaveAttribute('type', 'password');
    expect(keyInput).toHaveValue('');
    expect(keyInput).toHaveAttribute(
      'placeholder', "Leave empty to keep the saved key / use the provider's configured key",
    );
    expect(within(dialog).queryByTestId('api-key-saved')).not.toBeInTheDocument();

    await userEvent.type(within(dialog).getByLabelText(/Endpoint URL/i), GEMINI_BASE);
    await userEvent.type(keyInput, 'sk-test-typed');
    await userEvent.click(within(dialog).getByRole('button', { name: /Test connection/i }));
    await within(dialog).findByTestId('endpoint-test-result');
    expect(testBodies(spy)[0]).toMatchObject({ api_key: 'sk-test-typed', model_id: 'voyage-3.5' });

    await userEvent.click(within(dialog).getByRole('button', { name: /^Save$/i }));
    await waitFor(() => expect(postedModels(spy)).toHaveLength(1));
    expect(postedModels(spy)[0]).toMatchObject({ api_key: 'sk-test-typed' });
    expect(postedModels(spy)[0]).not.toHaveProperty('clear_api_key');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(JSON.stringify({ ...localStorage })).not.toContain('sk-test-typed');
    expect(JSON.stringify({ ...sessionStorage })).not.toContain('sk-test-typed');

    // Re-opening the dialog starts with an empty key field.
    const again = await openEdit('voyage-3.5');
    expect(within(again).getByLabelText('API key')).toHaveValue('');
  });

  test('an empty key field sends no api_key (the saved / provider key is kept)', async () => {
    const spy = mockFetch({ access: TENANT_ADMIN, registry: KEY_REGISTRY, testBody: EMBED_OK });
    renderPage();
    const dialog = await openEdit();
    await userEvent.click(within(dialog).getByRole('button', { name: /Test connection/i }));
    await within(dialog).findByTestId('endpoint-test-result');
    expect(testBodies(spy)[0]).not.toHaveProperty('api_key');
    await userEvent.click(within(dialog).getByRole('button', { name: /^Save$/i }));
    await waitFor(() => expect(postedModels(spy)).toHaveLength(1));
    expect(postedModels(spy)[0]).not.toHaveProperty('api_key');
    expect(postedModels(spy)[0]).not.toHaveProperty('clear_api_key');
  });

  test('a saved key shows "Key saved" (never the key); Remove key sends clear_api_key', async () => {
    const spy = mockFetch({ access: TENANT_ADMIN, registry: KEY_REGISTRY });
    renderPage();
    const dialog = await openEdit();
    expect(within(dialog).getByTestId('api-key-saved')).toHaveTextContent('Key saved');
    expect(within(dialog).getByLabelText('API key')).toHaveValue('');

    await userEvent.click(within(dialog).getByRole('button', { name: 'Remove key' }));
    expect(within(dialog).getByText(/saved key is removed on Save/i)).toBeInTheDocument();
    // Undo restores it, then remove again.
    await userEvent.click(within(dialog).getByRole('button', { name: 'Undo' }));
    expect(within(dialog).getByTestId('api-key-saved')).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Remove key' }));

    await userEvent.click(within(dialog).getByRole('button', { name: /^Save$/i }));
    await waitFor(() => expect(postedModels(spy)).toHaveLength(1));
    expect(postedModels(spy)[0]).toMatchObject({ clear_api_key: true, model_id: 'gemini-embedding-001' });
    expect(postedModels(spy)[0]).not.toHaveProperty('api_key');
  });

  test('Output dimensions is shown only for Embeddings, pre-filled, and sent on save and test', async () => {
    const spy = mockFetch({ access: TENANT_ADMIN, registry: KEY_REGISTRY, testBody: EMBED_OK });
    renderPage();
    const dialog = await openEdit();
    const dims = within(dialog).getByLabelText(/Output dimensions/i);
    expect(dims).toHaveValue(1536);
    expect(within(dialog).getByText(/must equal the vector index width/i)).toHaveTextContent('1536');

    await userEvent.click(within(dialog).getByRole('button', { name: /Test connection/i }));
    const result = await within(dialog).findByTestId('endpoint-test-result');
    expect(testBodies(spy)[0]).toMatchObject({ output_dimensions: 1536 });
    expect(within(result).getByTestId('endpoint-test-dimensions')).toHaveTextContent(
      'Vector width 1536-d (requested 1536) · vector index 1536-d (EMBEDDING_DIM)',
    );
    expect(within(result).queryByTestId('endpoint-test-dimension-mismatch')).not.toBeInTheDocument();

    await userEvent.clear(dims);
    await userEvent.type(dims, '768');
    expect(within(dialog).getByText(/768-d does not match the 1536-d vector index/i)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: /^Save$/i }));
    await waitFor(() => expect(postedModels(spy)).toHaveLength(1));
    expect(postedModels(spy)[0]).toMatchObject({ output_dimensions: 768 });

    // Not an embedding model: no field, nothing sent.
    const add = await openAdd();
    expect(within(add).queryByLabelText(/Output dimensions/i)).not.toBeInTheDocument();
    await userEvent.type(within(add).getByLabelText(/Model ID/i), 'chat-model');
    await userEvent.click(within(add).getByRole('button', { name: /^Save$/i }));
    await waitFor(() => expect(postedModels(spy)).toHaveLength(2));
    expect(postedModels(spy)[1]).not.toHaveProperty('output_dimensions');
  });

  test('an empty Output dimensions field sends null (native width); an invalid one blocks Save', async () => {
    const spy = mockFetch({ access: TENANT_ADMIN, registry: KEY_REGISTRY });
    renderPage();
    const dialog = await openEdit();
    const dims = within(dialog).getByLabelText(/Output dimensions/i);
    await userEvent.clear(dims);
    await userEvent.type(dims, '9000');
    await userEvent.click(within(dialog).getByRole('button', { name: /^Save$/i }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(/whole number from 1 to 8192/);
    expect(postedModels(spy)).toHaveLength(0);

    await userEvent.clear(dims);
    await userEvent.click(within(dialog).getByRole('button', { name: /^Save$/i }));
    await waitFor(() => expect(postedModels(spy)).toHaveLength(1));
    expect(postedModels(spy)[0].output_dimensions).toBeNull();
  });

  test('the test result shows a clear dimension mismatch warning', async () => {
    mockFetch({
      access: TENANT_ADMIN, registry: KEY_REGISTRY,
      testBody: {
        ...EMBED_OK, detail: '3072-dimension embedding', dimensions: 3072, requested_dimensions: null,
        dimension_mismatch: true,
      },
    });
    renderPage();
    const dialog = await openEdit();
    await userEvent.clear(within(dialog).getByLabelText(/Output dimensions/i));
    await userEvent.click(within(dialog).getByRole('button', { name: /Test connection/i }));
    const warning = await within(dialog).findByTestId('endpoint-test-dimension-mismatch');
    expect(warning).toHaveTextContent(
      'Dimension mismatch: the model returns 3072-d vectors but the vector index is 1536-d',
    );
    expect(warning).toHaveTextContent('Set Output dimensions to 1536');
    expect(warning).toHaveTextContent('EMBEDDING_DIM=3072');
  });

  test('the test result warns when the endpoint ignored the requested dimensions', async () => {
    mockFetch({
      access: TENANT_ADMIN, registry: KEY_REGISTRY,
      testBody: { ...EMBED_OK, dimensions: 3072, dimensions_ignored: true, dimension_mismatch: true },
    });
    renderPage();
    const dialog = await openEdit();
    await userEvent.click(within(dialog).getByRole('button', { name: /Test connection/i }));
    expect(await within(dialog).findByTestId('endpoint-test-result')).toHaveTextContent(
      'The endpoint ignored the requested output dimensions: asked for 1536, got 3072.',
    );
  });

  test('a chat-looking model id with Embeddings selected shows a non-blocking hint', async () => {
    const spy = mockFetch({ access: TENANT_ADMIN, registry: KEY_REGISTRY });
    renderPage();
    const dialog = await openAdd();
    await userEvent.type(within(dialog).getByLabelText(/Model ID/i), 'gemini-2.5-flash');
    expect(within(dialog).queryByTestId('chat-model-hint')).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Embeddings' }));
    expect(within(dialog).getByTestId('chat-model-hint')).toHaveTextContent(
      'This looks like a chat model; embedding models are usually named …-embedding-…',
    );
    // Not blocking: Save still posts.
    await userEvent.click(within(dialog).getByRole('button', { name: /^Save$/i }));
    await waitFor(() => expect(postedModels(spy)).toHaveLength(1));

    const dialog2 = await openAdd();
    await userEvent.type(within(dialog2).getByLabelText(/Model ID/i), 'gemini-embedding-001');
    await userEvent.click(within(dialog2).getByRole('button', { name: 'Embeddings' }));
    expect(within(dialog2).queryByTestId('chat-model-hint')).not.toBeInTheDocument();
  });

  test.each([
    ['HTTP 400: { "error": { "code": 400, "message": "Please pass a valid API key", "status": "INVALID_ARGUMENT" } }'],
    ['HTTP 401: Unauthorized'],
    ['HTTP 403: {"error":"forbidden"}'],
  ])('an API-key rejection (%s) explains how to fix it', async (error) => {
    mockFetch({
      access: TENANT_ADMIN, registry: KEY_REGISTRY,
      testBody: { ...EMBED_OK, ok: false, error, detail: '' },
    });
    renderPage();
    const dialog = await openEdit();
    await userEvent.click(within(dialog).getByRole('button', { name: /Test connection/i }));
    const failed = await within(dialog).findByTestId('endpoint-test-result');
    expect(failed).toHaveTextContent(
      "The provider rejected the API key (or none was sent). Add a key above, or set the provider's key on the server.",
    );
    expect(failed).toHaveTextContent(`Connection failed: ${error}`);
  });

  test('other failures keep the plain error text', async () => {
    mockFetch({
      access: TENANT_ADMIN, registry: KEY_REGISTRY,
      testBody: { ...EMBED_OK, ok: false, error: 'HTTP 400: model not found', detail: '' },
    });
    renderPage();
    const dialog = await openEdit();
    await userEvent.click(within(dialog).getByRole('button', { name: /Test connection/i }));
    const failed = await within(dialog).findByTestId('endpoint-test-result');
    expect(failed).toHaveTextContent('Connection failed: HTTP 400: model not found');
    expect(failed).not.toHaveTextContent(/rejected the API key/);
  });
});

describe('ModelRegistryPage — capability coverage reflects what can actually serve', () => {
  const COVERAGE = {
    total: 4,
    capabilities: [
      {
        capability: 'text_generation',
        status: 'ready',
        ready_count: 1,
        selected_model_id: 'qwen-lan',
        fallback_model_ids: [],
        order_mode: 'cost',
        preference: [],
        models: [
          // Saved with its own key, no URL: ready (it used to say "No API key").
          model({ provider: 'groq', model_id: 'own-key-llm', rank: 1, has_api_key: true, servable: true }),
          // Named in env, nothing behind it.
          model({ provider: 'openai', model_id: 'gpt-4o', rank: 2, source: 'env', servable: false }),
        ],
      },
      {
        capability: 'embedding',
        status: 'not_ready',
        ready_count: 0,
        selected_model_id: null,
        fallback_model_ids: [],
        order_mode: 'cost',
        preference: [],
        models: [
          model({ provider: 'openai', model_id: 'text-embedding-3-small', capabilities: ['embedding'], rank: 1, source: 'env', servable: false }),
        ],
      },
    ],
  };

  test('the coverage strip says ready / not usable / not configured per capability', async () => {
    mockFetch({ registry: COVERAGE });
    renderPage();
    const strip = await screen.findByTestId('capability-coverage');
    expect(within(strip).getByTestId('coverage-text_generation')).toHaveAttribute('data-state', 'ready');
    expect(within(strip).getByTestId('coverage-text_generation')).toHaveTextContent('Reasoning ready (1)');
    expect(within(strip).getByTestId('coverage-embedding')).toHaveAttribute('data-state', 'not_ready');
    expect(within(strip).getByTestId('coverage-embedding')).toHaveTextContent('Embeddings not usable');
    for (const cap of ['vision', 'ocr', 'rerank']) {
      expect(within(strip).getByTestId(`coverage-${cap}`)).toHaveAttribute('data-state', 'none');
    }
    expect(screen.getByTestId('capability-count-text_generation')).toHaveTextContent('1 of 2 ready');
    expect(screen.getByTestId('capability-count-embedding')).toHaveTextContent('0 of 1 ready');
  });

  test('an own-key model is not "No API key"; an env model nothing serves is "Not served"', async () => {
    mockFetch({ registry: COVERAGE });
    renderPage();
    const ownKey = (await screen.findByText('own-key-llm')).closest('li') as HTMLElement;
    expect(within(ownKey).queryByText('No API key')).not.toBeInTheDocument();
    expect(within(ownKey).getByText('Key saved')).toBeInTheDocument();
    expect(ownKey.className).not.toMatch(/opacity-60/);
    const envOnly = screen.getByText('gpt-4o').closest('li') as HTMLElement;
    expect(within(envOnly).getByText('Not served')).toBeInTheDocument();
    expect(within(envOnly).queryByText('Provider key')).not.toBeInTheDocument();
    expect(envOnly.className).toMatch(/opacity-60/);
    expect(screen.getAllByText(/named in the server configuration, but nothing is configured/i).length)
      .toBeGreaterThan(0);
  });

  test('an older backend without status fields falls back to provider_ready', async () => {
    mockFetch();
    renderPage();
    const strip = await screen.findByTestId('capability-coverage');
    expect(within(strip).getByTestId('coverage-text_generation')).toHaveTextContent('Reasoning ready (2)');
    expect(within(strip).getByTestId('coverage-embedding')).toHaveAttribute('data-state', 'ready');
  });
});
