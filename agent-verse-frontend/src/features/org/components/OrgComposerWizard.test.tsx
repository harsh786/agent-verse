import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { OrgComposerWizard } from './OrgComposerWizard';

const COMPOSE_RESULT = {
  org_id: 'org-42',
  name: 'CashFlow AI',
  departments: [
    { id: 'd1', name: 'Finance Ops', purpose: 'Reconcile the books', capability_domains: ['accounting', 'forecasting'] },
    { id: 'd2', name: 'Engineering', purpose: 'Build the platform', capability_domains: ['backend'] },
  ],
  initial_missions: [{ id: 'm1', title: 'Automate reconciliation' }],
  autonomy_level: 3,
  composition_method: 'llm',
};

function mockFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/v1/org/compose') && method === 'POST')
      return new Response(JSON.stringify(COMPOSE_RESULT), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderWizard(props: React.ComponentProps<typeof OrgComposerWizard> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <OrgComposerWizard {...props} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('OrgComposerWizard', () => {
  test('renders step 1 with the compose action disabled until a description is entered', async () => {
    mockFetch();
    renderWizard();
    expect(screen.getByRole('heading', { name: /Organisation Composer/i })).toBeInTheDocument();
    const compose = screen.getByRole('button', { name: /Compose organisation with AI/i });
    expect(compose).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Describe your organisation/i), 'A fintech startup');
    expect(compose).toBeEnabled();
  });

  test('adding a goal renders it as a removable chip', async () => {
    mockFetch();
    renderWizard();
    await userEvent.type(screen.getByLabelText('Add a goal'), 'Reduce churn');
    await userEvent.click(screen.getByRole('button', { name: 'Add goal' }));
    expect(screen.getByText('Reduce churn')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Remove goal: Reduce churn/i })).toBeInTheDocument();
  });

  test('composing POSTs to /v1/org/compose and renders the returned structure', async () => {
    const spy = mockFetch();
    renderWizard();
    await userEvent.type(screen.getByLabelText(/Describe your organisation/i), 'A fintech startup');
    await userEvent.click(screen.getByRole('button', { name: /Compose organisation with AI/i }));

    // Step 2 renders the AI-composed org name, departments and missions.
    expect(await screen.findByText('CashFlow AI')).toBeInTheDocument();
    expect(screen.getByText('Finance Ops')).toBeInTheDocument();
    expect(screen.getByText('Reconcile the books')).toBeInTheDocument();
    expect(screen.getByText('2 Departments')).toBeInTheDocument();
    expect(screen.getByText('Automate reconciliation')).toBeInTheDocument();

    expect(
      spy.mock.calls.some(([u, i]) =>
        String(u).includes('/v1/org/compose') && (i as RequestInit)?.method === 'POST',
      ),
    ).toBe(true);
  });

  test('completing the wizard calls onComplete with the new org id and name', async () => {
    mockFetch();
    const onComplete = vi.fn();
    renderWizard({ onComplete });
    await userEvent.type(screen.getByLabelText(/Describe your organisation/i), 'A fintech startup');
    await userEvent.click(screen.getByRole('button', { name: /Compose organisation with AI/i }));
    await screen.findByText('CashFlow AI');
    // Step 2 → Step 3.
    await userEvent.click(screen.getByRole('button', { name: /Continue to confirm/i }));
    // Step 3 → Launch.
    await userEvent.click(await screen.findByRole('button', { name: /Launch organisation/i }));
    expect(onComplete).toHaveBeenCalledWith('org-42', 'CashFlow AI');
  });

  test('the close button invokes onClose', async () => {
    mockFetch();
    const onClose = vi.fn();
    renderWizard({ onClose });
    await userEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
