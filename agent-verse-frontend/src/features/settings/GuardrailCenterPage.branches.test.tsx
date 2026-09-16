import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { GuardrailCenterPage } from './GuardrailCenterPage';

const RULES = [
  { id: 'gr-1', name: 'Block PII', rule_type: 'pii_detection', severity: 'critical',
    enabled: true, layers: ['goal', 'final'], config: {}, created_at: '2026-01-01T00:00:00Z' },
  { id: 'gr-2', name: 'Length Limit', rule_type: 'length_limit', severity: 'medium',
    enabled: false, layers: ['tool_args'], config: { max_length: 500 }, created_at: '2026-01-02T00:00:00Z' },
];

const STATS = {
  total_24h: 5, total_all: 20, risk_score_p95: 0.82,
  by_severity: { critical: 3, high: 2, medium: 1, low: 0 },
  by_layer: { goal: 4, tool_args: 2 },
  top_categories: [{ category: 'pii_leak', count: 9 }, { category: 'toxicity', count: 3 }],
};

const VIOLATIONS = [
  { id: 'v1', guardrail_name: 'Block PII', type: 'pii_detection', severity: 'critical',
    message: 'SSN detected in output', created_at: '2026-01-01T10:00:00Z' },
];

interface MockOpts {
  rules?: unknown[];
  violations?: unknown[];
  test?: unknown;
}

function mockFetch(opts: MockOpts = {}) {
  const {
    rules = RULES,
    violations = [],
    test: testResult = { passed: false, risk_score: 0.9, violations: [{ type: 'pii_detection', severity: 'critical', message: 'blocked content' }] },
  } = opts;
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = ((init as RequestInit | undefined)?.method ?? 'GET').toUpperCase();
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

    if (url.includes('/guardrails/violations')) return json(violations);
    if (url.includes('/guardrails/stats')) return json(STATS);
    if (url.includes('/guardrails/test')) return json(testResult);
    if (/\/guardrails\/[^/?]+$/.test(url) && (method === 'PUT' || method === 'DELETE')) return json({});
    if (url.includes('/guardrails') && method === 'POST') return json({ id: 'gr-new' });
    if (url.includes('/guardrails')) return json(rules);
    return json([]);
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><GuardrailCenterPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'enterprise', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('GuardrailCenterPage — branches', () => {
  test('Dashboard tab renders KPI cards and top categories', async () => {
    mockFetch();
    renderPage();
    await userEvent.click(screen.getByTestId('tab-dashboard'));
    expect(await screen.findByTestId('stats-cards')).toBeInTheDocument();
    expect(screen.getByText('Last 24h')).toBeInTheDocument();
    expect(screen.getByText('Top Violation Categories')).toBeInTheDocument();
    expect(screen.getByText('pii leak')).toBeInTheDocument();
  });

  test('toggling a rule PUTs to /guardrails/:id', async () => {
    const spy = mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByText('Block PII')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('toggle-gr-1'));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/guardrails/gr-1') && (i as RequestInit)?.method === 'PUT'),
      ).toBe(true),
    );
  });

  test('deleting a rule confirms then DELETEs to /guardrails/:id', async () => {
    const spy = mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByText('Block PII')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('delete-gr-1'));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/guardrails/gr-1') && (i as RequestInit)?.method === 'DELETE'),
      ).toBe(true),
    );
  });

  test('severity filter with no matches shows the filtered empty state', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByText('Block PII')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /^low$/i }));
    expect(await screen.findByText(/No rules match filter/i)).toBeInTheDocument();
  });

  test('creating a new rule POSTs to /guardrails', async () => {
    const spy = mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('new-rule-btn')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('new-rule-btn'));
    expect(await screen.findByText('New Guardrail')).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText(/Block PII in outputs/i), 'My Rule');
    await userEvent.click(screen.getByRole('button', { name: /Create Rule/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).endsWith('/guardrails') && (i as RequestInit)?.method === 'POST'),
      ).toBe(true),
    );
  });

  test('editing a rule opens the edit modal and PUTs the update', async () => {
    const spy = mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByText('Block PII')).toBeInTheDocument());
    const row = screen.getByTestId('guardrail-item-gr-1');
    await userEvent.click(within(row).getByRole('button', { name: 'Edit' }));
    expect(await screen.findByText('Edit Guardrail')).toBeInTheDocument();
    // The shared modal resets its name field, so give it a value to enable Save.
    await userEvent.type(screen.getByPlaceholderText(/Block PII in outputs/i), 'Renamed rule');
    await userEvent.click(screen.getByRole('button', { name: /Save Changes/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/guardrails/gr-1') && (i as RequestInit)?.method === 'PUT'),
      ).toBe(true),
    );
  });

  test('domain template quick-start creates rules via POST', async () => {
    const spy = mockFetch({ rules: [] });
    renderPage();
    const hipaa = await screen.findByRole('button', { name: /HIPAA \(2 rules\)/i });
    await userEvent.click(hipaa);
    await waitFor(() =>
      expect(spy.mock.calls.filter(([u, i]) =>
        String(u).endsWith('/guardrails') && (i as RequestInit)?.method === 'POST').length,
      ).toBeGreaterThanOrEqual(1),
    );
  });

  test('Violations tab renders a table of violations', async () => {
    mockFetch({ violations: VIOLATIONS });
    renderPage();
    await userEvent.click(screen.getByTestId('tab-violations'));
    expect(await screen.findByTestId('violations-table')).toBeInTheDocument();
    expect(screen.getByText('SSN detected in output')).toBeInTheDocument();
  });

  test('Violations tab shows empty state when clean', async () => {
    mockFetch({ violations: [] });
    renderPage();
    await userEvent.click(screen.getByTestId('tab-violations'));
    expect(await screen.findByText(/No violations/i)).toBeInTheDocument();
  });

  test('Test Playground runs a test and renders the Blocked result', async () => {
    const spy = mockFetch();
    renderPage();
    await userEvent.click(screen.getByTestId('tab-test'));
    await userEvent.type(await screen.findByTestId('test-input'), 'my ssn is 123-45-6789');
    await userEvent.click(screen.getByTestId('run-test-btn'));
    expect(await screen.findByText('Blocked')).toBeInTheDocument();
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/guardrails/test') && (i as RequestInit)?.method === 'POST'),
      ).toBe(true),
    );
  });
});
