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
        model({ provider: 'custom', model_id: 'cheap-llm', rank: 1 }),
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
        { model_id: 'grok-mini', display_name: 'Grok mini', capabilities: ['text_generation'], cost_per_1k_input: 0.0003, cost_per_1k_output: 0.0005, supports_tools: true, supports_vision: false, quality_score: 0.7, already_configured: true },
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

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const headersOf = (init?: RequestInit) => (init?.headers ?? {}) as Record<string, string>;

/**
 * Default: the caller is not a tenant admin, so the backend wants the platform
 * admin key; typing 'admin-secret' unlocks modification via admin_key.
 */
function mockFetch(opts: { access?: Access; registry?: unknown; postStatus?: number; postBody?: unknown } = {}) {
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
    const reasoning = screen.getByRole('heading', { name: 'Reasoning' }).closest('div.rounded-2xl') as HTMLElement;
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
    expect(await screen.findByRole('alert')).toHaveTextContent(/Model ID and at least one capability/i);
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
  });

  test('empty capability groups render a no-model hint', async () => {
    mockFetch({ registry: { total: 0, capabilities: [] } });
    renderPage();
    await waitFor(() =>
      expect(screen.getAllByText(/No model configured for/i)).toHaveLength(5),
    );
    expect(screen.getByText(/No model configured for embeddings/i)).toBeInTheDocument();
  });

  test('does not fetch the registry when no api key is present', async () => {
    useAuthStore.setState({ apiKey: '', tenantId: 't', plan: 'free', isAuthenticated: false });
    const spy = mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getAllByText(/No model configured for/i)).toHaveLength(5),
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
