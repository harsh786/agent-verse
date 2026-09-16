import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ModelControlCenter } from './ModelControlCenter';

const MODELS = {
  models: [
    {
      provider: 'anthropic', model_id: 'claude-x', display_name: 'Claude X',
      capabilities: ['text_generation', 'tool_use', 'vision'],
      quality_score: 0.92, cost_per_1k_input: 0.003, context_window: 200000,
      health: { is_healthy: true },
    },
    {
      provider: 'openai', model_id: 'gpt-y', display_name: 'GPT Y',
      capabilities: ['text_generation'],
      quality_score: 0.8, cost_per_1k_input: 0.001, context_window: 128000,
      health: { is_healthy: false },
    },
  ],
};

const HEALTH = {
  providers: [
    { provider: 'anthropic', is_healthy: true, avg_latency_ms: 120 },
    { provider: 'openai', is_healthy: false, avg_latency_ms: 0 },
  ],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function mockFetch(opts: { models?: unknown; pending?: boolean } = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/models/health')) return json(HEALTH);
    if (url.includes('/models/test') && method === 'POST')
      return json({ status: 'ok', model: 'Claude X', latency_ms: 88 });
    if (url.includes('/models')) {
      if (opts.pending) return new Promise<Response>(() => {});
      return json(opts.models ?? MODELS);
    }
    return json({});
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><ModelControlCenter /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ModelControlCenter', () => {
  test('renders header, model cards and the provider count summary', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByRole('heading', { name: /Model Control Center/i })).toBeInTheDocument();
    expect(await screen.findByText('Claude X')).toBeInTheDocument();
    expect(screen.getByText('GPT Y')).toBeInTheDocument();
    expect(screen.getByText(/2 models across 2 providers/i)).toBeInTheDocument();
  });

  test('shows the loading skeletons while the models query is pending', () => {
    mockFetch({ pending: true });
    const { container } = renderPage();
    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0);
  });

  test('provider health strip renders latency and health', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByText('120ms')).toBeInTheDocument();
    expect(screen.getByText('Not tested')).toBeInTheDocument();
  });

  test('selecting a provider filter refetches with the provider query param', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('Claude X');
    await userEvent.click(screen.getByRole('button', { name: 'anthropic' }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u]) => String(u).includes('/models?provider=anthropic'))).toBe(true),
    );
  });

  test('test-connection button POSTs to /models/test with provider + model_id', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('Claude X');
    await userEvent.click(screen.getAllByRole('button', { name: /Test Connection/i })[0]);
    await waitFor(() => {
      const post = spy.mock.calls.find(([u, i]) =>
        String(u).includes('/models/test') && (i as RequestInit)?.method === 'POST');
      expect(post).toBeTruthy();
      const body = JSON.parse(String((post?.[1] as RequestInit)?.body ?? '{}'));
      expect(body.provider).toBe('anthropic');
      expect(body.model_id).toBe('claude-x');
    });
  });

  test('renders the zero-state summary when no models are configured', async () => {
    mockFetch({ models: { models: [] } });
    renderPage();
    expect(await screen.findByText(/0 models across 0 providers/i)).toBeInTheDocument();
  });
});
