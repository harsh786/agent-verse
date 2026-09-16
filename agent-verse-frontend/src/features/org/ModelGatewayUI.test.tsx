import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ModelGatewayUI } from './ModelGatewayUI';

const MODELS = [
  {
    model_id: 'claude-sonnet', provider: 'anthropic', profile: 'premium',
    calls_24h: 1200, tokens_in_24h: 1000, tokens_out_24h: 500, cost_usd_24h: 4.5,
    avg_latency_ms: 820, success_rate: 0.99, fallback_count: 0, is_primary: true, health: 'healthy',
  },
  {
    model_id: 'gpt-oss-20b', provider: 'openai', profile: 'worker',
    calls_24h: 300, tokens_in_24h: 100, tokens_out_24h: 50, cost_usd_24h: 1.25,
    avg_latency_ms: 450, success_rate: 0.9, fallback_count: 2, is_primary: false, health: 'degraded',
  },
];

function mockModels(list: unknown[] = MODELS) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/analytics/models'))
      return new Response(JSON.stringify({ models: list }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderUI() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><ModelGatewayUI orgId="org-1" /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ModelGatewayUI', () => {
  test('renders the header and one card per model with the primary badge', async () => {
    mockModels();
    renderUI();
    expect(screen.getByText('Model Gateway')).toBeInTheDocument();
    expect(await screen.findByText('claude-sonnet')).toBeInTheDocument();
    expect(screen.getByText('gpt-oss-20b')).toBeInTheDocument();
    expect(screen.getByText('Primary')).toBeInTheDocument();
  });

  test('surfaces per-model cost and the fallback warning', async () => {
    mockModels();
    renderUI();
    await screen.findByText('claude-sonnet');
    expect(screen.getByText('$4.50')).toBeInTheDocument();
    // gpt-oss-20b triggered 2 fallbacks
    expect(screen.getByText(/2 fallbacks triggered/i)).toBeInTheDocument();
  });

  test('the summary stats reflect the model count and total cost', async () => {
    mockModels();
    renderUI();
    await screen.findByText('claude-sonnet');
    // Two models, total cost $5.75
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('$5.75')).toBeInTheDocument();
  });

  test('renders the empty state when no usage data is returned', async () => {
    mockModels([]);
    renderUI();
    expect(await screen.findByText('No model usage data yet.')).toBeInTheDocument();
  });
});
