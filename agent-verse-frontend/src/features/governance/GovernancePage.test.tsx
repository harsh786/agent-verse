import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useEmergencyStore } from '@/stores/emergency';
import { GovernancePage } from './GovernancePage';

// Suppress SSE hook
vi.mock('@/lib/sse/useEventStream', () => ({
  useEventStream: () => ({ connected: false, events: [] }),
}));

function renderGovernancePage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <GovernancePage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const POLICY = {
  policy_id: 'pol-1',
  name: 'block-shell',
  description: 'Block all shell commands',
  tools_pattern: 'shell:*',
  action: 'deny',
  priority: 10,
};

const APPROVAL = {
  request_id: 'req-abc',
  goal_id: 'goal-xyz',
  action: 'Deploy to production',
  risk_level: 'critical',
  status: 'pending',
};

const AUDIT_EVENT = {
  event_id: 'evt-001',
  goal_id: 'goal-xyz',
  tool_name: 'shell:execute',
  action_level: 'deny',
  outcome: 'blocked',
  approver: null,
  note: null,
};

const BUDGET = { tenant_id: 'tenant-1', per_goal_usd: 10.0, per_tenant_daily_usd: 500.0 };

function mockFetch(overrides: Record<string, unknown> = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    // Default: return empty arrays or 200 OK for most routes
    if (url.includes('/governance/policies') && (init?.method ?? 'GET') === 'GET')
      return new Response(JSON.stringify(overrides['policies'] ?? []), { status: 200 });
    if (url.includes('/governance/approvals') && (init?.method ?? 'GET') === 'GET')
      return new Response(JSON.stringify(overrides['approvals'] ?? []), { status: 200 });
    if (url.includes('/governance/audit'))
      return new Response(JSON.stringify(overrides['audit'] ?? []), { status: 200 });
    if (url.includes('/governance/budget') && (init?.method ?? 'GET') === 'GET')
      return new Response(JSON.stringify(overrides['budget'] ?? BUDGET), { status: 200 });
    if (url.includes('/governance/approvals/sla-stats'))
      return new Response(JSON.stringify(overrides['sla'] ?? {}), { status: 200 });
    if (url.includes('/costs/summary'))
      return new Response(JSON.stringify({ total_cost_usd: 0, cost_by_day: [], cost_by_model: {}, daily_budget_usd: 500, budget_utilization: 0 }), { status: 200 });
    if (url.includes('/costs/anomalies'))
      return new Response(JSON.stringify([]), { status: 200 });
    return new Response(JSON.stringify({}), { status: 200 });
  });
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 'tenant-1', plan: 'enterprise', isAuthenticated: true });
  useEmergencyStore.setState({ isActive: false, activatedAt: null, cancelledGoals: 0, rejectedApprovals: 0 });
});
afterEach(() => vi.restoreAllMocks());

// ═══════════════════════════════════════════════════════════════════════════════
// POLICIES TAB
// ═══════════════════════════════════════════════════════════════════════════════

describe('GovernancePage — Policies tab', () => {
  test('renders tab navigation with all four tabs', () => {
    mockFetch();
    renderGovernancePage();
    expect(screen.getByTestId('tab-policies')).toBeInTheDocument();
    expect(screen.getByTestId('tab-approvals')).toBeInTheDocument();
    expect(screen.getByTestId('tab-audit')).toBeInTheDocument();
    expect(screen.getByTestId('tab-budget')).toBeInTheDocument();
  });

  test('shows empty state when no policies are configured', async () => {
    mockFetch({ policies: [] });
    renderGovernancePage();
    await waitFor(() =>
      expect(screen.getByTestId('policies-empty')).toBeInTheDocument()
    );
  });

  test('lists existing policies with name, pattern, and action', async () => {
    mockFetch({ policies: [POLICY] });
    renderGovernancePage();
    await waitFor(() => expect(screen.getByText('block-shell')).toBeInTheDocument());
    expect(screen.getByText('shell:*')).toBeInTheDocument();
    expect(screen.getByText('deny')).toBeInTheDocument();
  });

  test('shows priority badge for each policy', async () => {
    mockFetch({ policies: [POLICY] });
    renderGovernancePage();
    await waitFor(() => expect(screen.getByText('10')).toBeInTheDocument());
  });

  test('shows policy form when "New Policy" button clicked', async () => {
    mockFetch({ policies: [] });
    renderGovernancePage();
    await waitFor(() => expect(screen.getByTestId('policies-empty')).toBeInTheDocument());
    await userEvent.click(screen.getByText(/New Policy/));
    expect(screen.getByTestId('policy-form')).toBeInTheDocument();
    expect(screen.getByTestId('policy-name-input')).toBeInTheDocument();
    expect(screen.getByTestId('policy-pattern-input')).toBeInTheDocument();
  });

  test('creates a policy via POST on save', async () => {
    let postCalled = false;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/governance/policies') && init?.method === 'POST') {
        postCalled = true;
        return new Response(JSON.stringify(POLICY), { status: 201 });
      }
      return new Response(JSON.stringify([]), { status: 200 });
    });
    renderGovernancePage();
    await waitFor(() => expect(screen.getByTestId('policies-empty')).toBeInTheDocument());
    await userEvent.click(screen.getByText(/New Policy/));
    await userEvent.type(screen.getByTestId('policy-name-input'), 'block-shell');
    await userEvent.type(screen.getByTestId('policy-pattern-input'), 'shell:*');
    await userEvent.click(screen.getByTestId('save-policy-btn'));
    await waitFor(() => expect(postCalled).toBe(true));
  });

  test('deletes a policy via DELETE', async () => {
    let deleteCalled = false;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/governance/policies/pol-1') && init?.method === 'DELETE') {
        deleteCalled = true;
        return new Response(null, { status: 204 });
      }
      return new Response(JSON.stringify([POLICY]), { status: 200 });
    });
    renderGovernancePage();
    await waitFor(() => expect(screen.getByText('block-shell')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('delete-policy-pol-1'));
    await waitFor(() => expect(deleteCalled).toBe(true));
  });

  test('shows simulate button', async () => {
    mockFetch({ policies: [] });
    renderGovernancePage();
    await waitFor(() => expect(screen.getByTestId('policies-empty')).toBeInTheDocument());
    expect(screen.getByText(/Simulate/)).toBeInTheDocument();
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// APPROVALS TAB
// ═══════════════════════════════════════════════════════════════════════════════

describe('GovernancePage — Approvals tab', () => {
  async function goToApprovals() {
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('tab-approvals'));
  }

  test('shows empty state when no pending approvals', async () => {
    mockFetch({ approvals: [] });
    await goToApprovals();
    await waitFor(() => expect(screen.getByTestId('approvals-empty')).toBeInTheDocument());
  });

  test('shows approval card with risk badge', async () => {
    mockFetch({ approvals: [APPROVAL] });
    await goToApprovals();
    await waitFor(() => expect(screen.getByTestId('approval-card')).toBeInTheDocument());
    expect(screen.getByTestId('risk-badge')).toBeInTheDocument();
    expect(screen.getByText('critical')).toBeInTheDocument();
  });

  test('shows approve and reject buttons', async () => {
    mockFetch({ approvals: [APPROVAL] });
    await goToApprovals();
    await waitFor(() => expect(screen.getByTestId(`approve-btn-${APPROVAL.request_id}`)).toBeInTheDocument());
    expect(screen.getByTestId(`reject-btn-${APPROVAL.request_id}`)).toBeInTheDocument();
  });

  test('calls approve endpoint on click', async () => {
    let approveCalled = false;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes(`/approvals/${APPROVAL.request_id}/approve`) && init?.method === 'POST') {
        approveCalled = true;
        return new Response(JSON.stringify({ status: 'approved' }), { status: 200 });
      }
      return new Response(JSON.stringify([APPROVAL]), { status: 200 });
    });
    await goToApprovals();
    await waitFor(() => expect(screen.getByTestId(`approve-btn-${APPROVAL.request_id}`)).toBeInTheDocument());
    await userEvent.click(screen.getByTestId(`approve-btn-${APPROVAL.request_id}`));
    await waitFor(() => expect(approveCalled).toBe(true));
  });

  test('calls reject endpoint on click', async () => {
    let rejectCalled = false;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes(`/approvals/${APPROVAL.request_id}/reject`) && init?.method === 'POST') {
        rejectCalled = true;
        return new Response(JSON.stringify({ status: 'rejected' }), { status: 200 });
      }
      return new Response(JSON.stringify([APPROVAL]), { status: 200 });
    });
    await goToApprovals();
    await waitFor(() => expect(screen.getByTestId(`reject-btn-${APPROVAL.request_id}`)).toBeInTheDocument());
    await userEvent.click(screen.getByTestId(`reject-btn-${APPROVAL.request_id}`));
    await waitFor(() => expect(rejectCalled).toBe(true));
  });

  test('shows batch toolbar when approval is selected', async () => {
    mockFetch({ approvals: [APPROVAL] });
    await goToApprovals();
    await waitFor(() => expect(screen.getByTestId('approval-card')).toBeInTheDocument());
    const checkbox = screen.getAllByRole('checkbox')[0];
    await userEvent.click(checkbox);
    await waitFor(() => expect(screen.getByTestId('batch-toolbar')).toBeInTheDocument());
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// AUDIT TAB
// ═══════════════════════════════════════════════════════════════════════════════

describe('GovernancePage — Audit tab', () => {
  async function goToAudit() {
    mockFetch({ audit: [AUDIT_EVENT] });
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('tab-audit'));
  }

  test('shows audit filters panel', async () => {
    mockFetch({ audit: [] });
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('tab-audit'));
    await waitFor(() => expect(screen.getByTestId('audit-filters')).toBeInTheDocument());
  });

  test('renders audit event rows', async () => {
    await goToAudit();
    await waitFor(() => expect(screen.getByTestId('audit-row')).toBeInTheDocument());
    expect(screen.getByText('shell:execute')).toBeInTheDocument();
  });

  test('shows export JSON and CSV buttons', async () => {
    await goToAudit();
    await waitFor(() => expect(screen.getByTestId('export-json-btn')).toBeInTheDocument());
    expect(screen.getByTestId('export-csv-btn')).toBeInTheDocument();
  });

  test('shows action level badge on audit row', async () => {
    await goToAudit();
    await waitFor(() => expect(screen.getByText('deny')).toBeInTheDocument());
  });

  test('empty state shown when no audit events match filters', async () => {
    mockFetch({ audit: [] });
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('tab-audit'));
    await waitFor(() => expect(screen.getByTestId('audit-empty')).toBeInTheDocument());
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// BUDGET TAB
// ═══════════════════════════════════════════════════════════════════════════════

describe('GovernancePage — Budget tab', () => {
  async function goToBudget() {
    mockFetch({ budget: BUDGET });
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('tab-budget'));
  }

  test('shows Budget Limits heading', async () => {
    await goToBudget();
    await waitFor(() => expect(screen.getByText('Budget Limits')).toBeInTheDocument());
  });

  test('shows per-goal and per-tenant inputs', async () => {
    await goToBudget();
    await waitFor(() => expect(screen.getByLabelText(/per-goal limit/i)).toBeInTheDocument());
    expect(screen.getByLabelText(/daily tenant limit/i)).toBeInTheDocument();
  });

  test('shows budget utilization gauge section', async () => {
    await goToBudget();
    await waitFor(() => {
      // There can be multiple "Budget Utilization" texts (stat card + gauge header)
      const els = screen.queryAllByText('Budget Utilization');
      expect(els.length).toBeGreaterThanOrEqual(1);
    });
  });

  test('save button appears when budget is changed', async () => {
    await goToBudget();
    await waitFor(() => expect(screen.getByLabelText(/per-goal limit/i)).toBeInTheDocument());
    await userEvent.clear(screen.getByLabelText(/per-goal limit/i));
    await userEvent.type(screen.getByLabelText(/per-goal limit/i), '25');
    await waitFor(() => expect(screen.getByTestId('save-budget-btn')).toBeInTheDocument());
  });

  test('calls PUT /governance/budget on save', async () => {
    let saveCalled = false;
    // Use the shared mockFetch helper for base routes, override only budget PUT
    mockFetch({ budget: BUDGET });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/governance/budget') && init?.method === 'PUT') {
        saveCalled = true;
        return new Response(JSON.stringify(BUDGET), { status: 200 });
      }
      // Route governance endpoints to sensible defaults
      if (url.includes('/governance/policies') && (init?.method ?? 'GET') === 'GET')
        return new Response(JSON.stringify([]), { status: 200 });
      if (url.includes('/governance/approvals') && (init?.method ?? 'GET') === 'GET')
        return new Response(JSON.stringify([]), { status: 200 });
      if (url.includes('/governance/audit'))
        return new Response(JSON.stringify([]), { status: 200 });
      if (url.includes('/governance/budget'))
        return new Response(JSON.stringify(BUDGET), { status: 200 });
      if (url.includes('/costs/'))
        return new Response(JSON.stringify({}), { status: 200 });
      return new Response(JSON.stringify({}), { status: 200 });
    });
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('tab-budget'));
    await waitFor(() => expect(screen.getByLabelText(/per-goal limit/i)).toBeInTheDocument());
    await userEvent.clear(screen.getByLabelText(/per-goal limit/i));
    await userEvent.type(screen.getByLabelText(/per-goal limit/i), '25');
    await waitFor(() => expect(screen.getByTestId('save-budget-btn')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('save-budget-btn'));
    await waitFor(() => expect(saveCalled).toBe(true));
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// EMERGENCY STOP
// ═══════════════════════════════════════════════════════════════════════════════

describe('GovernancePage — Emergency Stop', () => {
  test('shows emergency stop button', async () => {
    mockFetch();
    renderGovernancePage();
    expect(screen.getByTestId('emergency-stop-btn')).toBeInTheDocument();
  });

  test('shows confirmation dialog after clicking emergency stop', async () => {
    mockFetch();
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('emergency-stop-btn'));
    expect(screen.getByText(/Halt all agent execution/i)).toBeInTheDocument();
    expect(screen.getByText(/Confirm Stop/)).toBeInTheDocument();
  });

  test('calls POST /emergency-stop on confirm and shows banner', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/governance/emergency-stop') && init?.method === 'POST') {
        return new Response(
          JSON.stringify({ status: 'emergency_stop_activated', cancelled_goals: 3, rejected_approvals: 1 }),
          { status: 200 }
        );
      }
      return new Response(JSON.stringify([]), { status: 200 });
    });
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('emergency-stop-btn'));
    await userEvent.click(screen.getByText(/Confirm Stop/));
    await waitFor(() => expect(screen.getByTestId('emergency-banner')).toBeInTheDocument());
    expect(screen.getByText(/Emergency Stop Active/i)).toBeInTheDocument();
    expect(screen.getByText(/3 goals cancelled/)).toBeInTheDocument();
  });

  test('shows active banner and stats when emergency store is active', () => {
    useEmergencyStore.setState({
      isActive: true,
      activatedAt: new Date().toISOString(),
      cancelledGoals: 5,
      rejectedApprovals: 2,
    });
    mockFetch();
    renderGovernancePage();
    expect(screen.getByTestId('emergency-banner')).toBeInTheDocument();
    expect(screen.getByText(/5 goals cancelled/)).toBeInTheDocument();
  });
});
