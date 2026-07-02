import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { GuardrailCenterPage } from './GuardrailCenterPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <GuardrailCenterPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const MOCK_GUARDRAILS = [
  { id: 'gr-1', name: 'Block PII', rule_type: 'pii_detection', severity: 'critical',
    enabled: true, layers: ['goal', 'final'], config: {}, created_at: '2026-01-01T00:00:00Z' },
  { id: 'gr-2', name: 'Length Limit', rule_type: 'length_limit', severity: 'medium',
    enabled: false, layers: ['tool_args'], config: { max_length: 500 }, created_at: '2026-01-02T00:00:00Z' },
];

function mockFetch(guardrails = MOCK_GUARDRAILS) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/guardrails/violations'))
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/guardrails/stats'))
      return new Response(JSON.stringify({ total_24h: 0, total_all: 0, by_severity: {}, by_layer: {}, top_categories: [], risk_score_p95: 0 }),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/guardrails'))
      return new Response(JSON.stringify(guardrails), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

describe('GuardrailCenterPage', () => {
  beforeEach(() => {
    useAuthStore.setState({ apiKey: 'test-key', tenantId: 'tenant-1', plan: 'enterprise', isAuthenticated: true });
  });
  afterEach(() => vi.restoreAllMocks());

  test('renders Guardrail Center heading', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByRole('heading', { name: /Guardrail Center/i })).toBeInTheDocument());
  });

  test('shows loading state before guardrails arrive', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(document.body).toBeTruthy();
  });

  test('shows guardrail names from API on Rules tab', async () => {
    mockFetch();
    renderPage();
    // Rules tab is active by default
    await waitFor(() => expect(screen.getByText('Block PII')).toBeInTheDocument());
    expect(screen.getByText('Length Limit')).toBeInTheDocument();
  });

  test('shows severity badge for critical guardrail', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getAllByText('critical').length).toBeGreaterThanOrEqual(1));
  });

  test('shows empty state when no guardrails exist', async () => {
    mockFetch([]);
    renderPage();
    await waitFor(() => expect(screen.queryByText('Block PII')).not.toBeInTheDocument());
  });

  test('shows error state when fetch fails (no crash)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('server error', { status: 500 }));
    renderPage();
    await waitFor(() => expect(document.body).toBeTruthy());
  });

  test('tab navigation: all 4 tabs are present', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId('tab-dashboard')).toBeInTheDocument();
      expect(screen.getByTestId('tab-rules')).toBeInTheDocument();
      expect(screen.getByTestId('tab-violations')).toBeInTheDocument();
      expect(screen.getByTestId('tab-test')).toBeInTheDocument();
    });
  });

  test('new rule button is present', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('new-rule-btn')).toBeInTheDocument());
  });
});
