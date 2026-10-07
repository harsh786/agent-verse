import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AgentDetailPage } from './AgentDetailPage';
import { AutonomyRevalidationNotice } from './AutonomyRevalidationNotice';

const PENDING = {
  state: 'pending' as const,
  reason: 'config_changed_pending_eval',
  from_mode: 'fully-autonomous',
  eval_suite_id: 'suite-9',
  run_id: 'run-1',
};

function agent(overrides: Record<string, unknown>) {
  return {
    agent_id: 'agent-001',
    name: 'Payments Agent',
    autonomy_mode: 'bounded-autonomous',
    status: 'active',
    connector_ids: [],
    ...overrides,
  };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={['/agents/agent-001']}>
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route path="/agents/:agentId" element={<AgentDetailPage />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

function mockAgent(body: unknown) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    const payload = /\/agents\/agent-001$/.test(url) ? body : [];
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  });
}

describe('AutonomyRevalidationNotice', () => {
  test('pending: re-validating, will return to fully-autonomous if the suite passes', () => {
    render(<AutonomyRevalidationNotice revalidation={PENDING} />);
    const notice = screen.getByRole('status');
    expect(notice).toHaveTextContent(/Re-validating/);
    expect(notice).toHaveTextContent(/will return to fully-autonomous if the eval suite passes/);
    expect(notice).toHaveTextContent(/suite-9/);
  });

  test('failed: stays bounded and shows the result', () => {
    render(
      <AutonomyRevalidationNotice
        revalidation={{
          ...PENDING,
          state: 'failed',
          pass_rate: 0.4,
          min_pass_rate_required: 0.8,
          error: 'pass rate 40.0% is below the 80.0% threshold.',
        }}
      />,
    );
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent(/Re-validation failed/);
    expect(alert).toHaveTextContent(/stays bounded-autonomous/);
    expect(alert).toHaveTextContent(/Pass rate 40%, 80% required/);
  });

  test('nothing for no, promoted or cancelled re-validations', () => {
    const { container, rerender } = render(<AutonomyRevalidationNotice revalidation={null} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<AutonomyRevalidationNotice revalidation={{ ...PENDING, state: 'promoted' }} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<AutonomyRevalidationNotice revalidation={{ ...PENDING, state: 'cancelled' }} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('AgentDetailPage re-validation state', () => {
  beforeEach(() => {
    useAuthStore.setState({ apiKey: 'test-key', tenantId: 'tenant-1', isAuthenticated: true });
  });
  afterEach(() => vi.restoreAllMocks());

  test('a demoted agent shows that it is re-validating', async () => {
    mockAgent(agent({ autonomy_revalidation: PENDING, pending_promotion: true }));
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId('autonomy-revalidation')).toHaveTextContent(
        /will return to fully-autonomous if the eval suite passes/,
      ),
    );
    expect(screen.getByTestId('agent-autonomy')).toHaveTextContent('bounded-autonomous');
  });

  test('an agent with no re-validation shows no notice', async () => {
    mockAgent(agent({ autonomy_mode: 'fully-autonomous', autonomy_revalidation: null }));
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId('agent-name')).toHaveTextContent('Payments Agent'),
    );
    expect(screen.queryByTestId('autonomy-revalidation')).not.toBeInTheDocument();
  });
});
