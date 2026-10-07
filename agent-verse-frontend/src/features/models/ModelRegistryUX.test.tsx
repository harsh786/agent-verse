import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Link, MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { ModelRegistryPage } from './ModelRegistryPage';
import { probeErrorKind, validateModelForm } from './modelFormHelpers';

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
  servable: true,
  source: 'override',
  base_url: null,
  ...over,
  key: `${over.provider}/${over.model_id}`,
});

const REGISTRY = {
  total: 5,
  capabilities: [
    {
      capability: 'text_generation',
      status: 'ready',
      ready_count: 2,
      selected_model_id: 'fast-llm',
      fallback_model_ids: ['smart-llm'],
      order_mode: 'preference',
      preference: ['onprem/fast-llm', 'nvidia/smart-llm'],
      models: [
        model({ provider: 'onprem', model_id: 'fast-llm', rank: 1, thinking: 'off', base_url: 'http://10.0.0.9:8000/v1' }),
        model({ provider: 'nvidia', model_id: 'smart-llm', rank: 2, thinking: 'on', thinking_budget_tokens: 2048 }),
        model({ provider: 'groq', model_id: 'keyless-llm', rank: 3, provider_ready: false, servable: false }),
      ],
    },
    {
      capability: 'embedding',
      status: 'ready',
      ready_count: 1,
      selected_model_id: 'good-embed',
      fallback_model_ids: [],
      order_mode: 'preference',
      preference: ['voyage/wide-embed', 'voyage/good-embed'],
      models: [
        model({
          provider: 'voyage', model_id: 'wide-embed', capabilities: ['embedding'], rank: 1,
          refused: true, refusal_reason: 'the model returns 3072-d vectors but the vector index is 1536-d',
          dimensions: 3072, collection_compatible: true, collection_chunk_table: 'knowledge_chunks_3072',
        }),
        model({ provider: 'voyage', model_id: 'good-embed', capabilities: ['embedding'], rank: 2 }),
      ],
    },
  ],
};

const RESOLUTION = {
  capabilities: [
    {
      capability: 'reasoning', label: 'Reasoning', routed: true,
      model: { model_id: 'fast-llm', provider: 'onprem', servable: true },
      source: 'registry_order', source_label: 'Registry order',
      fallbacks: [{ model_id: 'smart-llm', provider: 'nvidia', servable: true }],
      warning: null, note: null,
    },
    {
      capability: 'embedding', label: 'Embeddings', routed: true,
      model: { model_id: 'good-embed', provider: 'voyage', servable: true },
      source: 'env_pin', source_label: 'Environment pin', fallbacks: [], warning: null,
      note: 'Embeddings fail over only between endpoints serving the same model.',
    },
    {
      capability: 'vision', label: 'Vision', routed: true,
      model: { model_id: 'fast-llm', provider: 'onprem', servable: true },
      source: 'default', source_label: 'Provider default', fallbacks: [],
      warning: 'No Vision model: images go to the reasoning model fast-llm, which may not accept images.',
      note: null,
    },
    {
      capability: 'ocr', label: 'OCR', routed: false, model: null, source: 'none',
      source_label: 'Nothing resolves', fallbacks: [], warning: null, note: null,
    },
    {
      capability: 'rerank', label: 'Rerank', routed: true, model: null, source: 'none',
      source_label: 'Nothing resolves', fallbacks: [],
      warning: 'No reranker: retrieval results are not reranked.', note: null,
    },
  ],
  roles: [
    {
      task_type: 'planning', label: 'Planning', roles: ['answer_synthesis', 'planner', 'planning'],
      routed_by_goal_router: true, model: { model_id: 'smart-llm', provider: 'nvidia', servable: true },
      source: 'tenant_pin', source_label: 'Tenant routing policy',
      fallbacks: [{ model_id: 'fast-llm', provider: 'onprem', servable: true }], warning: null,
    },
    {
      task_type: 'judge', label: 'Judges', roles: ['eval_judge', 'guardrail_judge'],
      routed_by_goal_router: false, model: null, source: 'none', source_label: 'Nothing resolves',
      fallbacks: [], warning: 'No model resolves for judges.',
    },
  ],
  warnings: ['No reranker: retrieval results are not reranked.'],
};

const TENANT_ADMIN = { can_modify: true, via: 'tenant_admin', needs_admin_key: false, reason: '' };

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function mockFetch(opts: {
  registry?: unknown; resolution?: unknown; resolutionStatus?: number;
  testBody?: unknown; prefStatus?: number; prefBody?: unknown; deleteStatus?: number; deleteBody?: unknown;
} = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/models/configured/access')) return json(TENANT_ADMIN);
    if (url.includes('/models/resolution'))
      return json(opts.resolution ?? RESOLUTION, opts.resolutionStatus ?? 200);
    if (url.includes('/models/configured/test-endpoint')) return json(opts.testBody ?? {});
    if (url.includes('/models/preferences/') && method === 'PUT')
      return json(opts.prefBody ?? { status: 'saved' }, opts.prefStatus ?? 200);
    if (url.includes('/models/preferences/') && method === 'DELETE') return json({ status: 'reset' });
    if (url.includes('/models/configured') && method === 'POST') return json({ status: 'ok' });
    if (url.includes('/models/configured') && method === 'DELETE')
      return json(opts.deleteBody ?? { status: 'deleted' }, opts.deleteStatus ?? 200);
    if (url.includes('/models/configured')) return json(opts.registry ?? REGISTRY);
    return json({});
  });
}

function renderPage(extra?: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{extra}<ModelRegistryPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

const calls = (spy: ReturnType<typeof mockFetch>, pred: (url: string, init: RequestInit) => boolean) =>
  spy.mock.calls.filter(([u, i]) => pred(String(u), (i ?? {}) as RequestInit));
const bodyOf = (call: unknown[]) => JSON.parse(((call[1] as RequestInit).body as string) || '{}');
const posted = (spy: ReturnType<typeof mockFetch>) =>
  calls(spy, (u, i) => u.endsWith('/models/configured') && i.method === 'POST').map(bodyOf);
const tests = (spy: ReturnType<typeof mockFetch>) =>
  calls(spy, (u) => u.includes('/models/configured/test-endpoint')).map(bodyOf);

async function openAdd() {
  await screen.findByTestId('model-row-onprem/fast-llm');
  await userEvent.click(await screen.findByRole('button', { name: /Add Model/i }));
  return screen.getByRole('dialog');
}

async function openEdit(id: string) {
  await screen.findAllByText(id);
  await userEvent.click(await screen.findByRole('button', { name: `Edit ${id}` }));
  return screen.getByRole('dialog');
}

const sectionOf = (label: string) =>
  screen.getByRole('heading', { name: label, level: 2 }).closest('section') as HTMLElement;

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
  useToastStore.setState({ toasts: [] });
});
afterEach(() => vi.restoreAllMocks());

// ── 1. Thinking control ───────────────────────────────────────────────────────

describe('thinking control', () => {
  test('shown only for Reasoning; Auto by default; budget only for On; saved with the model', async () => {
    const spy = mockFetch();
    renderPage();
    const dialog = await openAdd();
    const control = within(dialog).getByTestId('thinking-control');
    expect(within(control).getByRole('radio', { name: 'Auto' })).toBeChecked();
    expect(within(dialog).queryByLabelText(/Thinking budget/i)).not.toBeInTheDocument();
    expect(control).toHaveTextContent(/retried once with thinking off/i);

    await userEvent.click(within(control).getByRole('radio', { name: 'On' }));
    const budget = within(dialog).getByLabelText(/Thinking budget/i);
    expect(budget).toHaveAttribute('aria-describedby', expect.stringContaining('thinking-budget-help'));
    await userEvent.type(budget, '4096');
    await userEvent.type(within(dialog).getByLabelText(/Model ID/i), 'qwen3-32b');
    await userEvent.click(within(dialog).getByRole('button', { name: /^Save$/ }));
    await waitFor(() => expect(posted(spy)).toHaveLength(1));
    expect(posted(spy)[0]).toMatchObject({ thinking: 'on', thinking_budget_tokens: 4096 });

    // Not a reasoning model: no control, nothing sent.
    const again = await openAdd();
    await userEvent.click(within(again).getByRole('button', { name: 'Reasoning' }));
    await userEvent.click(within(again).getByRole('button', { name: 'Embeddings' }));
    expect(within(again).queryByTestId('thinking-control')).not.toBeInTheDocument();
    await userEvent.type(within(again).getByLabelText(/Model ID/i), 'some-embedding');
    await userEvent.click(within(again).getByRole('button', { name: /^Save$/ }));
    await waitFor(() => expect(posted(spy)).toHaveLength(2));
    expect(posted(spy)[1]).not.toHaveProperty('thinking');
  });

  test('an invalid budget blocks Save with a field error', async () => {
    const spy = mockFetch();
    renderPage();
    const dialog = await openAdd();
    await userEvent.type(within(dialog).getByLabelText(/Model ID/i), 'm');
    await userEvent.click(within(dialog).getByRole('radio', { name: 'On' }));
    await userEvent.type(within(dialog).getByLabelText(/Thinking budget/i), '0');
    await userEvent.click(within(dialog).getByRole('button', { name: /^Save$/ }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(/Thinking budget: Budget must be a whole number/);
    expect(within(dialog).getByLabelText(/Thinking budget/i)).toHaveAttribute('aria-invalid', 'true');
    expect(posted(spy)).toHaveLength(0);
  });

  test('Edit pre-fills the saved thinking setting and budget', async () => {
    mockFetch();
    renderPage();
    const dialog = await openEdit('smart-llm');
    expect(within(dialog).getByRole('radio', { name: 'On' })).toBeChecked();
    expect(within(dialog).getByLabelText(/Thinking budget/i)).toHaveValue(2048);
  });

  test('rows show a thinking badge for reasoning models', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByTestId('thinking-badge-onprem/fast-llm')).toHaveTextContent('Thinking off');
    expect(screen.getByTestId('thinking-badge-nvidia/smart-llm')).toHaveTextContent('Thinking on · 2048 tok');
    expect(screen.getByTestId('thinking-badge-groq/keyless-llm')).toHaveTextContent('Thinking auto');
    expect(within(sectionOf('Embeddings')).queryByText(/Thinking/)).not.toBeInTheDocument();
  });

  test('a recommendation to turn thinking off is applied in one click; the user still saves', async () => {
    const spy = mockFetch({
      testBody: {
        ok: true, latency_ms: 812, probe: 'chat', model_listed: true, served_models: [], error: null,
        detail: "replied: 'OK' (after retrying with thinking off)",
        thinking: {
          mode: 'auto', thinking_model: true, reasoning_tokens: 16, disable_supported: true,
          disabled_works: true,
          recommendation: 'set "thinking": "off" for direct answers (auto otherwise spends the first call reasoning before retrying with thinking off)',
        },
      },
    });
    renderPage();
    const dialog = await openAdd();
    await userEvent.type(within(dialog).getByLabelText(/Model ID/i), 'qwen3');
    await userEvent.type(within(dialog).getByLabelText(/Endpoint URL/i), 'http://10.0.0.9:8000/v1');
    await userEvent.click(within(dialog).getByRole('button', { name: /Test connection/i }));
    expect(tests(spy)[0]).toMatchObject({ thinking: 'auto' });
    const rec = await within(dialog).findByTestId('thinking-recommendation');
    expect(within(dialog).getByTestId('probe-thinking')).toHaveTextContent(/Thinking model detected/);
    await userEvent.click(within(rec).getByRole('button', { name: 'Apply: thinking off' }));
    expect(within(dialog).getByRole('radio', { name: 'Off' })).toBeChecked();
    expect(within(rec).getByRole('status')).toHaveTextContent('Thinking set to Off — Save to keep it.');
    expect(within(rec).queryByRole('button', { name: 'Apply: thinking off' })).not.toBeInTheDocument();
    // Nothing was saved by applying it.
    expect(posted(spy)).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole('button', { name: /^Save$/ }));
    await waitFor(() => expect(posted(spy)).toHaveLength(1));
    expect(posted(spy)[0]).toMatchObject({ thinking: 'off' });
  });
});

// ── 2. Capability-specific test results ───────────────────────────────────────

describe('capability-aware Test connection', () => {
  async function runTest(testBody: unknown, caps: string[] = []) {
    const spy = mockFetch({ testBody });
    renderPage();
    const dialog = await openAdd();
    await userEvent.type(within(dialog).getByLabelText(/Model ID/i), 'm-1');
    await userEvent.type(within(dialog).getByLabelText(/Endpoint URL/i), 'http://10.0.0.9:8000/v1');
    for (const c of caps) await userEvent.click(within(dialog).getByRole('button', { name: c }));
    await userEvent.click(within(dialog).getByRole('button', { name: /Test connection/i }));
    return { spy, dialog, result: await within(dialog).findByTestId('endpoint-test-result') };
  }

  test('rerank: shows the scores, best first, and the latency', async () => {
    const { result } = await runTest({
      ok: true, latency_ms: 31, probe: 'rerank', model_listed: true, served_models: [], detail: '2 documents scored',
      error: null, error_kind: null,
      checks: [{
        probe: 'rerank', capabilities: ['rerank'], ok: true, latency_ms: 24.6, detail: '2 documents scored',
        error: null, error_kind: null, relevant_first: true,
        scores: [
          { index: 0, score: 0.97, document: 'Paris is the capital and largest city of France.' },
          { index: 1, score: 0.012, document: 'Bananas are a yellow fruit rich in potassium.' },
        ],
      }],
    }, ['Reasoning', 'Reranker']);
    expect(result).toHaveAttribute('role', 'status');
    const card = within(result).getByTestId('probe-check-rerank');
    expect(card).toHaveTextContent('Rerank');
    expect(within(card).getByText('Passed')).toBeInTheDocument();
    expect(within(card).getByTestId('probe-latency-rerank')).toHaveTextContent('25 ms');
    const scores = within(card).getByTestId('probe-rerank-scores');
    expect(scores).toHaveTextContent('0.970');
    expect(scores.textContent!.indexOf('Paris')).toBeLessThan(scores.textContent!.indexOf('Bananas'));
  });

  test('vision / OCR: shows the reply to the test image and whether the text was read', async () => {
    const { result, spy } = await runTest({
      ok: true, latency_ms: 640, probe: 'vision', model_listed: null, served_models: [], detail: "image understood: replied 'HELLO'",
      error: null, error_kind: null,
      checks: [{
        probe: 'vision', capabilities: ['ocr', 'vision'], ok: true, latency_ms: 640,
        detail: "image understood: replied 'HELLO'", error: null, error_kind: null,
        reply: 'HELLO', expected_text: 'HELLO', text_matched: true,
      }],
    }, ['Reasoning', 'Vision', 'OCR']);
    expect(tests(spy)[0].capabilities).toEqual(['vision', 'ocr']);
    const card = within(result).getByTestId('probe-check-vision');
    expect(card).toHaveTextContent('Vision / OCR (image)');
    expect(card).toHaveTextContent('covers ocr, vision');
    expect(within(card).getByTestId('probe-vision-reply')).toHaveTextContent('replied “HELLO”');
    expect(within(card).getByTestId('probe-vision-reply')).toHaveTextContent('Text read correctly.');
  });

  test('a capability the model does not support is named as such', async () => {
    const { result } = await runTest({
      ok: false, latency_ms: 50, probe: 'vision', model_listed: null, served_models: [], detail: '',
      error: 'HTTP 400: m-1 is not a multimodal model', error_kind: 'unsupported',
      checks: [{
        probe: 'vision', capabilities: ['vision'], ok: false, latency_ms: 50, detail: '',
        error: 'HTTP 400: m-1 is not a multimodal model', error_kind: 'unsupported',
      }],
    }, ['Reasoning', 'Vision']);
    expect(result).toHaveAttribute('role', 'alert');
    expect(within(result).getByTestId('endpoint-test-error-title')).toHaveTextContent('Capability not supported');
    expect(result).toHaveTextContent('Connection failed: HTTP 400: m-1 is not a multimodal model');
  });

  test('several checks: each card says whether it passed; the failing one explains why', async () => {
    const { result } = await runTest({
      ok: false, latency_ms: 120, probe: 'chat', model_listed: true, served_models: [], detail: "replied: 'OK'",
      error: 'vision: HTTP 401: invalid api key', error_kind: 'auth',
      checks: [
        { probe: 'chat', capabilities: ['text_generation'], ok: true, latency_ms: 80, detail: "replied: 'OK'", error: null, error_kind: null },
        { probe: 'vision', capabilities: ['vision'], ok: false, latency_ms: 40, detail: '', error: 'HTTP 401: invalid api key', error_kind: 'auth' },
      ],
    }, ['Vision']);
    expect(within(result).getByTestId('endpoint-test-error-title')).toHaveTextContent('API key rejected');
    expect(within(result).getByTestId('probe-check-chat')).toHaveAttribute('data-ok', 'true');
    const vision = within(result).getByTestId('probe-check-vision');
    expect(vision).toHaveAttribute('data-ok', 'false');
    expect(within(vision).getByText('API key rejected')).toBeInTheDocument();
    expect(vision).toHaveTextContent('HTTP 401: invalid api key');
  });

  test.each([
    ['unreachable', 'ConnectError: connection refused', 'Endpoint unreachable'],
    ['model_not_served', 'HTTP 404: The model `m-1` does not exist.', 'Model not served'],
  ])('error kind %s (%s) is mapped to its title', async (kind, error, title) => {
    const { result } = await runTest({
      ok: false, latency_ms: 5, probe: 'chat', model_listed: null, served_models: [], detail: '',
      error, error_kind: kind,
      checks: [{ probe: 'chat', capabilities: ['text_generation'], ok: false, latency_ms: 5, detail: '', error, error_kind: kind }],
    });
    expect(within(result).getByTestId('endpoint-test-error-title')).toHaveTextContent(title);
  });

  test('older backends without error_kind are classified from the message', () => {
    expect(probeErrorKind(undefined, 'HTTP 401: nope')).toBe('auth');
    expect(probeErrorKind(null, 'HTTP 404: model x not found')).toBe('model_not_served');
    expect(probeErrorKind(null, 'HTTP 404: Not Found')).toBe('unsupported');
    expect(probeErrorKind(null, 'ConnectError: refused')).toBe('unreachable');
    expect(probeErrorKind(null, 'Private host 10.0.0.1 is not allowed')).toBe('refused');
    expect(probeErrorKind(null, 'thinking model: spent the whole budget')).toBe('thinking_budget');
  });
});

// ── 3. Resolved models panel ─────────────────────────────────────────────────

describe('resolved models panel', () => {
  test('shows each capability with its model, source and fallbacks, and warns about gaps', async () => {
    mockFetch();
    renderPage();
    const panel = await screen.findByTestId('resolved-models');
    const reasoning = await within(panel).findByTestId('resolution-cap-reasoning');
    expect(reasoning).toHaveTextContent('fast-llm');
    expect(within(reasoning).getByTestId('resolution-source')).toHaveTextContent('Registry order');
    expect(reasoning).toHaveTextContent('smart-llm');
    const vision = within(panel).getByTestId('resolution-cap-vision');
    expect(within(vision).getByTestId('resolution-source')).toHaveTextContent('Provider default');
    expect(vision).toHaveTextContent('may not accept images');
    const rerank = within(panel).getByTestId('resolution-cap-rerank');
    expect(within(rerank).getByTestId('resolution-source')).toHaveTextContent('Nothing resolves');
    expect(within(panel).getByTestId('resolution-warnings')).toHaveTextContent('No usable model for Rerank');
  });

  test('a capability not routed by the registry says so honestly', async () => {
    mockFetch();
    renderPage();
    const ocr = await screen.findByTestId('resolution-cap-ocr');
    expect(within(ocr).getByTestId('resolution-not-routed')).toHaveTextContent('Not routed by the registry yet');
    // Not routed is not reported as "no usable model".
    expect(screen.getByTestId('resolution-warnings')).not.toHaveTextContent('OCR');
  });

  test('agent roles expand to show every role group with its source', async () => {
    mockFetch();
    renderPage();
    const toggle = await screen.findByRole('button', { name: /Agent roles \(5 roles in 2 groups\)/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const planning = screen.getByTestId('resolution-role-planning');
    expect(planning).toHaveTextContent('smart-llm');
    expect(planning).toHaveTextContent('planner');
    expect(within(planning).getByTestId('resolution-source')).toHaveTextContent('Tenant routing policy');
    expect(screen.getByTestId('resolution-role-judge')).toHaveTextContent('No model resolves for judges.');
  });

  test('a backend without the endpoint shows that resolution is unavailable', async () => {
    mockFetch({ resolution: {} });
    renderPage();
    expect(await screen.findByTestId('resolution-unavailable')).toBeInTheDocument();
  });

  test('a failing resolution request shows the backend message', async () => {
    mockFetch({ resolution: { detail: 'registry store unavailable' }, resolutionStatus: 400 });
    renderPage();
    const panel = await screen.findByTestId('resolved-models');
    expect(await within(panel).findByRole('alert')).toHaveTextContent('registry store unavailable');
  });
});

// ── 4. Preference order: keyboard, drag and drop, disabled rows, guard ──────────

describe('preference order', () => {
  test('keyboard: Arrow keys on the grip move the model, announce it and keep focus; dirty until saved', async () => {
    const spy = mockFetch();
    renderPage();
    const grip = await screen.findByRole('button', { name: /Reorder smart-llm, position 2 of 3/ });
    expect(grip).toHaveAttribute('aria-describedby', 'reorder-help-text_generation');
    grip.focus();
    await userEvent.keyboard('{ArrowUp}');
    const moved = await screen.findByRole('button', { name: /Reorder smart-llm, position 1 of 3/ });
    expect(moved).toHaveFocus();
    expect(screen.getByTestId('reorder-announcement-text_generation')).toHaveTextContent('smart-llm moved to position 1 of 3.');
    expect(screen.getByTestId('dirty-text_generation')).toHaveTextContent('Unsaved order');
    expect(sectionOf('Reasoning')).toHaveAttribute('data-dirty', 'true');

    await userEvent.click(screen.getByRole('button', { name: 'Save Reasoning order' }));
    await waitFor(() =>
      expect(calls(spy, (u, i) => u.includes('/models/preferences/text_generation') && i.method === 'PUT')).toHaveLength(1),
    );
    const [put] = calls(spy, (u, i) => u.includes('/models/preferences/text_generation') && i.method === 'PUT');
    expect(bodyOf(put)).toEqual({ order: ['nvidia/smart-llm', 'onprem/fast-llm', 'groq/keyless-llm'] });
    await waitFor(() => expect(useToastStore.getState().toasts.some((t) => t.message === 'Reasoning order saved')).toBe(true));
  });

  test('a not-servable model is disabled with its reason and cannot be moved first', async () => {
    mockFetch();
    renderPage();
    const row = await screen.findByTestId('model-row-groq/keyless-llm');
    expect(row).toHaveAttribute('aria-disabled', 'true');
    expect(row).toHaveAttribute('draggable', 'false');
    expect(within(row).getByTestId('skip-reason-groq/keyless-llm')).toHaveTextContent(/cannot be primary: No API key/);
    expect(within(row).getByRole('button', { name: 'Move keyless-llm up' })).toBeDisabled();
    const grip = within(row).getByRole('button', { name: /Reorder keyless-llm/ });
    expect(grip).toHaveAttribute('aria-disabled', 'true');
    grip.focus();
    await userEvent.keyboard('{ArrowUp}');
    expect(screen.queryByTestId('dirty-text_generation')).not.toBeInTheDocument();
  });

  test('a refused embedding model shows the refusal, is never primary and cannot be moved', async () => {
    mockFetch();
    renderPage();
    const row = await screen.findByTestId('model-row-voyage/wide-embed');
    expect(within(row).getByText('Refused')).toBeInTheDocument();
    expect(within(row).queryByText('Primary')).not.toBeInTheDocument();
    expect(row).toHaveTextContent('Cannot be the default embedder: the model returns 3072-d vectors');
    expect(within(row).getByRole('button', { name: 'Move wide-embed down' })).toBeDisabled();
    expect(within(screen.getByTestId('model-row-voyage/good-embed')).getByText('Primary')).toBeInTheDocument();
    // Per-collection embedders: it still serves knowledge collections of its own width.
    expect(within(row).getByText('3072-d collections')).toBeInTheDocument();
  });

  test('drag and drop reorders the list', async () => {
    mockFetch();
    renderPage();
    const fast = await screen.findByTestId('model-row-onprem/fast-llm');
    const smart = screen.getByTestId('model-row-nvidia/smart-llm');
    expect(smart).toHaveAttribute('draggable', 'true');
    const dt = { data: {} as Record<string, string>, setData(k: string, v: string) { this.data[k] = v; }, getData(k: string) { return this.data[k]; }, effectAllowed: '' };
    fireEvent.dragStart(smart, { dataTransfer: dt });
    fireEvent.dragOver(fast, { dataTransfer: dt });
    fireEvent.drop(fast, { dataTransfer: dt });
    await waitFor(() => expect(screen.getByTestId('dirty-text_generation')).toBeInTheDocument());
    const rows = within(sectionOf('Reasoning')).getAllByTestId(/^model-row-/);
    expect(rows.map((r) => r.getAttribute('data-testid'))).toEqual([
      'model-row-nvidia/smart-llm', 'model-row-onprem/fast-llm', 'model-row-groq/keyless-llm',
    ]);
  });

  test('Discard changes restores the saved order', async () => {
    mockFetch();
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Move smart-llm up' }));
    expect(screen.getByTestId('dirty-text_generation')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
    expect(screen.queryByTestId('dirty-text_generation')).not.toBeInTheDocument();
  });

  test('unsaved changes guard: beforeunload is blocked and an in-app link asks first', async () => {
    mockFetch();
    renderPage(<Link to="/agents">Agents</Link>);
    await userEvent.click(await screen.findByRole('button', { name: 'Move smart-llm up' }));
    const unload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);

    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const link = screen.getByRole('link', { name: 'Agents' });
    const click = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 });
    link.dispatchEvent(click);
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/unsaved preference-order changes/));
    expect(click.defaultPrevented).toBe(true);
  });

  test('no guard without unsaved changes', async () => {
    mockFetch();
    renderPage();
    await screen.findByTestId('model-row-onprem/fast-llm');
    const unload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(false);
  });

  test('a failed save shows the backend message inline and as a toast', async () => {
    mockFetch({ prefStatus: 400, prefBody: { detail: 'unknown model key groq/x' } });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Move smart-llm up' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save Reasoning order' }));
    expect(await within(sectionOf('Reasoning')).findByRole('alert')).toHaveTextContent('unknown model key groq/x');
    expect(useToastStore.getState().toasts.map((t) => t.message)).toContain(
      'Could not save the Reasoning order: unknown model key groq/x',
    );
  });
});

// ── 5. Validation, provider hints, empty states, dialog a11y, delete ──────────

describe('form validation and provider hints', () => {
  test('validates the base URL, costs and quality next to each field', async () => {
    const spy = mockFetch();
    renderPage();
    const dialog = await openAdd();
    await userEvent.type(within(dialog).getByLabelText(/Model ID/i), 'bad model');
    await userEvent.type(within(dialog).getByLabelText(/Endpoint URL/i), 'ftp://host/v1');
    const costIn = within(dialog).getByLabelText(/Cost \/ 1k input/i);
    await userEvent.clear(costIn);
    await userEvent.type(costIn, '-1');
    const quality = within(dialog).getByLabelText(/Quality/i);
    await userEvent.clear(quality);
    await userEvent.type(quality, '1.5');
    await userEvent.click(within(dialog).getByRole('button', { name: /^Save$/ }));
    const alert = await within(dialog).findByRole('alert');
    expect(alert).toHaveTextContent('Model ID cannot contain spaces');
    expect(alert).toHaveTextContent('must start with http:// or https://');
    expect(alert).toHaveTextContent('Cost cannot be negative');
    expect(alert).toHaveTextContent('Quality must be between 0 and 1');
    for (const label of [/Endpoint URL/i, /Cost \/ 1k input/i, /Quality/i]) {
      const input = within(dialog).getByLabelText(label);
      expect(input).toHaveAttribute('aria-invalid', 'true');
      const ids = input.getAttribute('aria-describedby')!.split(' ');
      expect(ids.some((i) => document.getElementById(i)?.className.includes('text-destructive'))).toBe(true);
    }
    expect(posted(spy)).toHaveLength(0);
    // Test connection refuses a bad URL too.
    expect(within(dialog).getByRole('button', { name: /Test connection/i })).toBeDisabled();
  });

  test('pure validation helper', () => {
    const base = {
      model_id: 'm', base_url: '', capabilities: ['text_generation'], cost_per_1k_input: '0',
      cost_per_1k_output: '0', quality_score: '0.5', output_dimensions: '', thinking: 'auto' as const,
      thinking_budget_tokens: '',
    };
    expect(validateModelForm(base)).toEqual({});
    expect(validateModelForm({ ...base, capabilities: [] }).capabilities).toBeTruthy();
    expect(validateModelForm({ ...base, base_url: 'http://<host>:8000/v1' }).base_url).toMatch(/placeholder/);
    expect(validateModelForm({ ...base, base_url: 'not a url' }).base_url).toBeTruthy();
    expect(validateModelForm({ ...base, cost_per_1k_output: 'abc' }).cost_per_1k_output).toBe('Enter a number');
  });

  test('the provider suggests a base URL as a hint and never overwrites typed input', async () => {
    mockFetch();
    renderPage();
    const dialog = await openAdd();
    const url = within(dialog).getByLabelText(/Endpoint URL/i);
    expect(url).toHaveAttribute('placeholder', 'https://integrate.api.nvidia.com/v1');
    await userEvent.selectOptions(within(dialog).getByLabelText('Provider'), 'ollama');
    expect(url).toHaveAttribute('placeholder', 'http://localhost:11434/v1');
    expect(url).toHaveValue('');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Use http://localhost:11434/v1' }));
    expect(url).toHaveValue('http://localhost:11434/v1');

    await userEvent.clear(url);
    await userEvent.type(url, 'http://gpu-box:9000/v1');
    await userEvent.selectOptions(within(dialog).getByLabelText('Provider'), 'gemini');
    expect(url).toHaveValue('http://gpu-box:9000/v1');
    expect(url).toHaveAttribute('placeholder', 'https://generativelanguage.googleapis.com/v1beta/openai');
    expect(within(dialog).queryByRole('button', { name: /^Use / })).not.toBeInTheDocument();
  });
});

describe('empty states, dialog accessibility and delete', () => {
  test('an empty capability offers to add one, with that capability preselected', async () => {
    mockFetch();
    renderPage();
    const empty = await screen.findByTestId('empty-ocr');
    expect(empty).toHaveTextContent('No OCR model yet — add one');
    await userEvent.click(within(empty).getByRole('button', { name: 'Add OCR model' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('button', { name: 'OCR' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(dialog).getByRole('button', { name: 'Reasoning' })).toHaveAttribute('aria-pressed', 'false');
  });

  test('shows loading skeletons until the registry arrives', async () => {
    let resolve!: (r: Response) => void;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/models/configured/access')) return json(TENANT_ADMIN);
      if (url.includes('/models/configured')) return new Promise<Response>((r) => { resolve = r; });
      return json({});
    });
    renderPage();
    expect(await screen.findByTestId('registry-loading')).toBeInTheDocument();
    resolve(json(REGISTRY));
    await waitFor(() => expect(screen.queryByTestId('registry-loading')).not.toBeInTheDocument());
  });

  test('the dialog focuses the first field, traps Tab and closes on Escape', async () => {
    mockFetch();
    renderPage();
    const addBtn = await screen.findByRole('button', { name: /Add Model/i });
    await screen.findByTestId('model-row-onprem/fast-llm');
    await userEvent.click(addBtn);
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveAccessibleName('Add / override a model');
    expect(within(dialog).getByLabelText(/Model ID/i)).toHaveFocus();
    const save = within(dialog).getByRole('button', { name: /^Save$/ });
    save.focus();
    await userEvent.tab();
    expect(within(dialog).getByLabelText(/Model ID/i)).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(save).toHaveFocus();
    // Focus lost to <body> (e.g. the focused button was replaced): Tab comes
    // back into the dialog and Escape still closes it.
    save.blur();
    expect(document.body).toHaveFocus();
    await userEvent.tab();
    expect(within(dialog).getByLabelText(/Model ID/i)).toHaveFocus();
    save.blur();
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(addBtn).toHaveFocus();
  });

  test('delete asks for confirmation; Cancel keeps the model', async () => {
    const spy = mockFetch();
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Remove smart-llm' }));
    const confirm = screen.getByRole('alertdialog', { name: 'Remove smart-llm?' });
    expect(confirm).toHaveTextContent(/removed from the registry for every tenant \(Reasoning\)/);
    await userEvent.click(within(confirm).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(calls(spy, (_u, i) => i.method === 'DELETE')).toHaveLength(0);
  });

  test('a failed delete shows the backend message in a toast', async () => {
    mockFetch({ deleteStatus: 403, deleteBody: { detail: 'Platform admin privileges required' } });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Remove smart-llm' }));
    await userEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Remove' }));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.map((t) => t.message)).toContain(
        'Could not remove smart-llm: Platform admin privileges required',
      ),
    );
  });

  test('the page copy is truthful and has no hardcoded LAN endpoint', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByText(/Register a model your provider can serve/)).toBeInTheDocument();
    const dialog = await openAdd();
    expect(dialog.innerHTML).not.toContain('192.168.');
  });
});
