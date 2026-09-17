import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useEmergencyStore } from '@/stores/emergency';
import { useToastStore } from '@/stores/toast';
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
    if (url.includes('/governance/approvals/sla-stats'))
      return new Response(JSON.stringify(overrides['sla'] ?? {}), { status: 200 });
    if (url.includes('/governance/approvals') && (init?.method ?? 'GET') === 'GET')
      return new Response(JSON.stringify(overrides['approvals'] ?? []), { status: 200 });
    if (url.includes('/governance/audit'))
      return new Response(JSON.stringify(overrides['audit'] ?? []), { status: 200 });
    if (url.includes('/governance/budget') && (init?.method ?? 'GET') === 'GET')
      return new Response(JSON.stringify(overrides['budget'] ?? BUDGET), { status: 200 });
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
  useToastStore.setState({ toasts: [] });
  if (typeof URL.createObjectURL !== 'function') {
    URL.createObjectURL = vi.fn(() => 'blob:mock-url');
  }
  if (typeof URL.revokeObjectURL !== 'function') {
    URL.revokeObjectURL = vi.fn();
  }
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
    await userEvent.click(screen.getByRole('button', { name: /delete policy/i }));
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

  test('cancelling the stop confirmation returns to the operational state', async () => {
    mockFetch();
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('emergency-stop-btn'));
    expect(screen.getByText(/Halt all agent execution/i)).toBeInTheDocument();
    await userEvent.click(screen.getByText('Cancel'));
    expect(screen.queryByText(/Halt all agent execution/i)).not.toBeInTheDocument();
    expect(screen.getByTestId('emergency-stop-btn')).toBeInTheDocument();
  });

  test('clears an active emergency stop via DELETE and resets the store', async () => {
    useEmergencyStore.setState({
      isActive: true,
      activatedAt: new Date().toISOString(),
      cancelledGoals: 5,
      rejectedApprovals: 2,
    });
    let deleteCalled = false;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/governance/emergency-stop') && init?.method === 'DELETE') {
        deleteCalled = true;
        return new Response(JSON.stringify({ status: 'cleared', tenant_id: 'tenant-1' }), { status: 200 });
      }
      return new Response(JSON.stringify([]), { status: 200 });
    });
    renderGovernancePage();
    expect(screen.getByTestId('emergency-banner')).toBeInTheDocument();
    await userEvent.click(screen.getByText(/Clear Emergency Stop/));
    await waitFor(() => expect(deleteCalled).toBe(true));
    await waitFor(() => expect(screen.queryByTestId('emergency-banner')).not.toBeInTheDocument());
    expect(useEmergencyStore.getState().isActive).toBe(false);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// POLICIES TAB — advanced flows
// ═══════════════════════════════════════════════════════════════════════════════

describe('GovernancePage — Policies tab advanced', () => {
  const POLICY_2 = {
    policy_id: 'pol-2',
    name: 'allow-jira',
    description: '',
    tools_pattern: 'jira:*',
    action: 'require_approval',
    priority: 3,
  };

  test('filters policies by search text (name and pattern)', async () => {
    mockFetch({ policies: [POLICY, POLICY_2] });
    renderGovernancePage();
    await waitFor(() => expect(screen.getByText('block-shell')).toBeInTheDocument());
    expect(screen.getByText('allow-jira')).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText('Search policies…'), 'jira');
    expect(screen.queryByText('block-shell')).not.toBeInTheDocument();
    expect(screen.getByText('allow-jira')).toBeInTheDocument();
  });

  test('selecting all policies via header checkbox enables bulk delete', async () => {
    mockFetch({ policies: [POLICY, POLICY_2] });
    renderGovernancePage();
    await waitFor(() => expect(screen.getByText('block-shell')).toBeInTheDocument());
    const table = screen.getByRole('table');
    const headerCheckbox = within(table).getAllByRole('checkbox')[0];
    await userEvent.click(headerCheckbox);
    expect(screen.getByText(/Delete 2/)).toBeInTheDocument();
  });

  test('bulk deletes selected policies', async () => {
    const deletedIds: string[] = [];
    mockFetch({ policies: [POLICY, POLICY_2] });
    renderGovernancePage();
    await waitFor(() => expect(screen.getByText('block-shell')).toBeInTheDocument());
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/governance/policies/') && init?.method === 'DELETE') {
        deletedIds.push(url.split('/').pop() as string);
        return new Response(null, { status: 204 });
      }
      if (url.includes('/governance/policies'))
        return new Response(JSON.stringify([POLICY, POLICY_2]), { status: 200 });
      return new Response(JSON.stringify([]), { status: 200 });
    });
    const table = screen.getByRole('table');
    const headerCheckbox = within(table).getAllByRole('checkbox')[0];
    await userEvent.click(headerCheckbox);
    await userEvent.click(screen.getByText(/Delete 2/));
    await waitFor(() => expect(deletedIds.sort()).toEqual(['pol-1', 'pol-2']));
  });

  test('toggling a single policy row checkbox selects it individually', async () => {
    mockFetch({ policies: [POLICY, POLICY_2] });
    renderGovernancePage();
    await waitFor(() => expect(screen.getByText('block-shell')).toBeInTheDocument());
    const rowCheckbox = screen.getByTestId(`delete-policy-${POLICY.policy_id}`)
      .closest('tr')!
      .querySelector('input[type="checkbox"]') as HTMLElement;
    await userEvent.click(rowCheckbox);
    expect(screen.getByText(/Delete 1/)).toBeInTheDocument();
    await userEvent.click(rowCheckbox);
    expect(screen.queryByText(/Delete 1/)).not.toBeInTheDocument();
  });

  test('shows time-restricted badge when policy has hour/day windows', async () => {
    mockFetch({
      policies: [{ ...POLICY, allowed_hours_utc: [9, 10], allowed_weekdays: [1, 2] }],
    });
    renderGovernancePage();
    await waitFor(() => expect(screen.getByText('Restricted')).toBeInTheDocument());
  });

  test('shows "Always" window label when no time restriction is set', async () => {
    mockFetch({ policies: [POLICY] });
    renderGovernancePage();
    await waitFor(() => expect(screen.getByText('Always')).toBeInTheDocument());
  });

  test('selects a tools pattern from the examples dropdown', async () => {
    mockFetch({ policies: [] });
    renderGovernancePage();
    await waitFor(() => expect(screen.getByTestId('policies-empty')).toBeInTheDocument());
    await userEvent.click(screen.getByText(/New Policy/));
    const select = screen.getByLabelText('Pattern examples');
    await userEvent.selectOptions(select, 'github:delete*');
    expect(screen.getByTestId('policy-pattern-input')).toHaveValue('github:delete*');
  });

  test('changes the action select to require_approval', async () => {
    mockFetch({ policies: [] });
    renderGovernancePage();
    await waitFor(() => expect(screen.getByTestId('policies-empty')).toBeInTheDocument());
    await userEvent.click(screen.getByText(/New Policy/));
    await userEvent.selectOptions(screen.getByTestId('policy-action-select'), 'require_approval');
    expect(screen.getByTestId('policy-action-select')).toHaveValue('require_approval');
  });

  test('enabling the time window reveals hour and weekday pickers and toggles selection', async () => {
    mockFetch({ policies: [] });
    renderGovernancePage();
    await waitFor(() => expect(screen.getByTestId('policies-empty')).toBeInTheDocument());
    await userEvent.click(screen.getByText(/New Policy/));
    await userEvent.click(screen.getByText('Restrict to time window'));
    expect(screen.getByText('Allowed hours (UTC)')).toBeInTheDocument();
    const hourBtn = screen.getByText('9');
    await userEvent.click(hourBtn);
    await userEvent.click(hourBtn); // toggle off, covers the deselect branch
    const dayBtn = screen.getByText('Wed');
    await userEvent.click(dayBtn);
    await userEvent.click(dayBtn); // toggle off
  });

  test('creates a policy with a time window applied', async () => {
    let sentBody: Record<string, unknown> | undefined;
    mockFetch({ policies: [] });
    renderGovernancePage();
    await waitFor(() => expect(screen.getByTestId('policies-empty')).toBeInTheDocument());
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/governance/policies') && init?.method === 'POST') {
        sentBody = JSON.parse(String(init.body));
        return new Response(JSON.stringify(POLICY), { status: 201 });
      }
      return new Response(JSON.stringify([]), { status: 200 });
    });
    await userEvent.click(screen.getByText(/New Policy/));
    await userEvent.type(screen.getByTestId('policy-name-input'), 'time-limited');
    await userEvent.type(screen.getByTestId('policy-pattern-input'), 'shell:*');
    await userEvent.click(screen.getByText('Restrict to time window'));
    await userEvent.click(screen.getByText('9'));
    await userEvent.click(screen.getByText('Wed'));
    await userEvent.click(screen.getByTestId('save-policy-btn'));
    await waitFor(() => expect(sentBody).toBeDefined());
    expect(sentBody?.allowed_hours_utc).toEqual([9]);
    expect(sentBody?.allowed_weekdays).toEqual([2]);
  });

  test('shows a validation toast when required fields are missing on create', async () => {
    mockFetch({ policies: [] });
    renderGovernancePage();
    await waitFor(() => expect(screen.getByTestId('policies-empty')).toBeInTheDocument());
    await userEvent.click(screen.getByText(/New Policy/));
    await userEvent.click(screen.getByTestId('save-policy-btn'));
    await waitFor(() =>
      expect(
        useToastStore.getState().toasts.some((t) => t.message === 'Name and Tools Pattern are required.')
      ).toBe(true)
    );
  });

  test('cancel button hides the create form', async () => {
    mockFetch({ policies: [] });
    renderGovernancePage();
    await waitFor(() => expect(screen.getByTestId('policies-empty')).toBeInTheDocument());
    await userEvent.click(screen.getByText(/New Policy/));
    expect(screen.getByTestId('policy-form')).toBeInTheDocument();
    await userEvent.click(screen.getByText('Cancel'));
    expect(screen.queryByTestId('policy-form')).not.toBeInTheDocument();
  });

  test('opens the simulate modal, runs a simulation, and closes it', async () => {
    mockFetch({ policies: [] });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/governance/policies/simulate') && init?.method === 'POST') {
        return new Response(
          JSON.stringify({ simulation_results: { 'shell:execute': 'DENY (matched block-shell)' } }),
          { status: 200 }
        );
      }
      if (url.includes('/governance/policies')) return new Response(JSON.stringify([]), { status: 200 });
      return new Response(JSON.stringify([]), { status: 200 });
    });
    renderGovernancePage();
    await waitFor(() => expect(screen.getByTestId('policies-empty')).toBeInTheDocument());
    await userEvent.click(screen.getByText(/Simulate/));
    expect(screen.getByText('Policy Simulator')).toBeInTheDocument();
    await userEvent.click(screen.getByText('Run Simulation'));
    await waitFor(() => expect(screen.getByText('shell:execute')).toBeInTheDocument());
    expect(screen.getByText('DENY (matched block-shell)')).toBeInTheDocument();
    const modal = screen.getByText('Policy Simulator').closest('div')!.parentElement!;
    const closeBtn = within(modal as HTMLElement).getAllByRole('button')[0];
    await userEvent.click(closeBtn);
    expect(screen.queryByText('Policy Simulator')).not.toBeInTheDocument();
  });

  test('simulate modal shows allow/approval result variants', async () => {
    mockFetch({ policies: [] });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/governance/policies/simulate') && init?.method === 'POST') {
        return new Response(
          JSON.stringify({
            simulation_results: {
              'jira:create_issue': 'REQUIRE_APPROVAL',
              'slack:post': 'ALLOW',
            },
          }),
          { status: 200 }
        );
      }
      return new Response(JSON.stringify([]), { status: 200 });
    });
    renderGovernancePage();
    await waitFor(() => expect(screen.getByTestId('policies-empty')).toBeInTheDocument());
    await userEvent.click(screen.getByText(/Simulate/));
    await userEvent.click(screen.getByText('Run Simulation'));
    await waitFor(() => expect(screen.getByText('REQUIRE_APPROVAL')).toBeInTheDocument());
    expect(screen.getByText('ALLOW')).toBeInTheDocument();
  });

  test('opens version history modal showing empty state, then closes it', async () => {
    mockFetch({ policies: [POLICY] });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/versions')) return new Response(JSON.stringify([]), { status: 200 });
      if (url.includes('/governance/policies')) return new Response(JSON.stringify([POLICY]), { status: 200 });
      return new Response(JSON.stringify([]), { status: 200 });
    });
    renderGovernancePage();
    await waitFor(() => expect(screen.getByText('block-shell')).toBeInTheDocument());
    await userEvent.click(screen.getByTitle('Version history'));
    expect(screen.getByText(/Version History/)).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText('No version history available (requires database).')).toBeInTheDocument()
    );
    const closeBtn = screen.getByText(/Version History/).parentElement!.parentElement!.querySelector('button')!;
    await userEvent.click(closeBtn);
    expect(screen.queryByText(/Version History/)).not.toBeInTheDocument();
  });

  test('rolls back a policy version from the history modal', async () => {
    const VERSIONS = [
      { id: 'v2', version_number: 2, is_active: true, changed_at: new Date().toISOString(), change_summary: 'latest', changed_by: 'alice' },
      { id: 'v1', version_number: 1, is_active: false, changed_at: new Date().toISOString(), change_summary: 'initial', changed_by: 'bob' },
    ];
    let rollbackCalled = false;
    mockFetch({ policies: [POLICY] });
    renderGovernancePage();
    await waitFor(() => expect(screen.getByText('block-shell')).toBeInTheDocument());
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/rollback') && init?.method === 'POST') {
        rollbackCalled = true;
        return new Response(JSON.stringify({ policy_id: 'pol-1', new_version: 3, rolled_back_to: 1, reason: 'x' }), { status: 200 });
      }
      if (url.includes('/versions')) return new Response(JSON.stringify(VERSIONS), { status: 200 });
      if (url.includes('/governance/policies')) return new Response(JSON.stringify([POLICY]), { status: 200 });
      return new Response(JSON.stringify([]), { status: 200 });
    });
    await userEvent.click(screen.getByTitle('Version history'));
    await waitFor(() => expect(screen.getByText('Active')).toBeInTheDocument());
    expect(screen.getByText('by alice')).toBeInTheDocument();
    await userEvent.click(screen.getByText('Rollback to this version'));
    await waitFor(() => expect(rollbackCalled).toBe(true));
    await waitFor(() => expect(screen.queryByText(/Version History/)).not.toBeInTheDocument());
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// APPROVALS TAB — advanced flows
// ═══════════════════════════════════════════════════════════════════════════════

describe('GovernancePage — Approvals tab advanced', () => {
  const SLA_STATS = {
    pending: 2,
    approved: 5,
    denied: 1,
    within_sla: 4,
    avg_resolution_seconds: 125,
  };

  const RESOLVED_APPROVAL = {
    request_id: 'req-resolved-1',
    goal_id: 'goal-old',
    action: 'Delete database',
    risk_level: 'high',
    status: 'approved',
  };

  test('renders SLA stats row with computed within-SLA percentage', async () => {
    mockFetch({ approvals: [APPROVAL], sla: SLA_STATS });
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('tab-approvals'));
    await waitFor(() => expect(screen.getByTestId('sla-stats')).toBeInTheDocument());
    expect(screen.getByText('Pending')).toBeInTheDocument();
    expect(screen.getByText('67%')).toBeInTheDocument(); // round(4/6*100)
    expect(screen.getByText(/avg 2m/)).toBeInTheDocument();
  });

  test('renders recently resolved approvals section', async () => {
    mockFetch({ approvals: [RESOLVED_APPROVAL] });
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('tab-approvals'));
    await waitFor(() => expect(screen.getByText('Recently Resolved')).toBeInTheDocument());
    expect(screen.getByText('Delete database')).toBeInTheDocument();
    expect(screen.getByText('approved')).toBeInTheDocument();
  });

  test('types a note before approving, sent through the approve mutation', async () => {
    let sentBody: Record<string, unknown> | undefined;
    mockFetch({ approvals: [APPROVAL] });
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('tab-approvals'));
    await waitFor(() => expect(screen.getByTestId('approval-card')).toBeInTheDocument());
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/approve') && init?.method === 'POST') {
        sentBody = JSON.parse(String(init.body));
        return new Response(JSON.stringify({ status: 'approved' }), { status: 200 });
      }
      return new Response(JSON.stringify([APPROVAL]), { status: 200 });
    });
    await userEvent.type(screen.getByPlaceholderText('Add a note (optional)'), 'looks fine');
    await userEvent.click(screen.getByTestId(`approve-btn-${APPROVAL.request_id}`));
    await waitFor(() => expect(sentBody?.note).toBe('looks fine'));
  });

  test('batch approves selected approvals', async () => {
    let batchBody: Record<string, unknown> | undefined;
    mockFetch({ approvals: [APPROVAL] });
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('tab-approvals'));
    await waitFor(() => expect(screen.getByTestId('approval-card')).toBeInTheDocument());
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/hitl/batch-approve') && init?.method === 'POST') {
        batchBody = JSON.parse(String(init.body));
        return new Response(JSON.stringify({ approved: 1, not_found: 0, rejected: 0 }), { status: 200 });
      }
      return new Response(JSON.stringify([APPROVAL]), { status: 200 });
    });
    await userEvent.click(screen.getAllByRole('checkbox')[0]);
    await waitFor(() => expect(screen.getByTestId('batch-toolbar')).toBeInTheDocument());
    await userEvent.click(screen.getByText('Batch Approve'));
    await waitFor(() => expect(batchBody).toBeDefined());
    expect(batchBody?.action).toBe('approve');
    expect(batchBody?.request_ids).toEqual([APPROVAL.request_id]);
  });

  test('batch rejects selected approvals', async () => {
    let batchBody: Record<string, unknown> | undefined;
    mockFetch({ approvals: [APPROVAL] });
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('tab-approvals'));
    await waitFor(() => expect(screen.getByTestId('approval-card')).toBeInTheDocument());
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/hitl/batch-approve') && init?.method === 'POST') {
        batchBody = JSON.parse(String(init.body));
        return new Response(JSON.stringify({ approved: 0, not_found: 0, rejected: 1 }), { status: 200 });
      }
      return new Response(JSON.stringify([APPROVAL]), { status: 200 });
    });
    await userEvent.click(screen.getAllByRole('checkbox')[0]);
    await waitFor(() => expect(screen.getByTestId('batch-toolbar')).toBeInTheDocument());
    await userEvent.click(screen.getByText('Batch Reject'));
    await waitFor(() => expect(batchBody).toBeDefined());
    expect(batchBody?.action).toBe('reject');
  });

  test('clears the selection via the Clear button in the batch toolbar', async () => {
    mockFetch({ approvals: [APPROVAL] });
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('tab-approvals'));
    await waitFor(() => expect(screen.getByTestId('approval-card')).toBeInTheDocument());
    await userEvent.click(screen.getAllByRole('checkbox')[0]);
    await waitFor(() => expect(screen.getByTestId('batch-toolbar')).toBeInTheDocument());
    await userEvent.click(screen.getByText('Clear'));
    expect(screen.queryByTestId('batch-toolbar')).not.toBeInTheDocument();
  });

  test('shows loading state while approvals are being fetched', async () => {
    let resolveFetch: (r: Response) => void;
    const pending = new Promise<Response>((res) => { resolveFetch = res; });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/governance/approvals') && !url.includes('sla-stats')) return pending;
      return new Response(JSON.stringify([]), { status: 200 });
    });
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('tab-approvals'));
    expect(screen.getByTestId('loading')).toBeInTheDocument();
    resolveFetch!(new Response(JSON.stringify([]), { status: 200 }));
    await waitFor(() => expect(screen.getByTestId('approvals-empty')).toBeInTheDocument());
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// AUDIT TAB — advanced flows
// ═══════════════════════════════════════════════════════════════════════════════

describe('GovernancePage — Audit tab advanced', () => {
  async function goToAudit(overrides: Record<string, unknown> = {}) {
    mockFetch({ audit: [AUDIT_EVENT], ...overrides });
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('tab-audit'));
    await waitFor(() => expect(screen.getByTestId('audit-row')).toBeInTheDocument());
  }

  test('applies filters and re-queries with the new params', async () => {
    let lastUrl = '';
    mockFetch({ audit: [AUDIT_EVENT] });
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('tab-audit'));
    await waitFor(() => expect(screen.getByTestId('audit-row')).toBeInTheDocument());
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/governance/audit')) {
        lastUrl = url;
        return new Response(JSON.stringify([AUDIT_EVENT]), { status: 200 });
      }
      return new Response(JSON.stringify([]), { status: 200 });
    });
    await userEvent.type(screen.getByLabelText('Goal ID'), 'goal-xyz');
    await userEvent.type(screen.getByLabelText('Tool name'), 'shell:execute');
    await userEvent.click(screen.getByTestId('apply-audit-filters'));
    await waitFor(() => expect(lastUrl).toContain('goal_id=goal-xyz'));
    expect(lastUrl).toContain('tool_name=shell%3Aexecute');
  });

  test('resets filters back to defaults', async () => {
    await goToAudit();
    await userEvent.type(screen.getByLabelText('Goal ID'), 'goal-xyz');
    await userEvent.click(screen.getByText('Reset'));
    expect(screen.getByLabelText('Goal ID')).toHaveValue('');
  });

  test('verifies the audit chain successfully', async () => {
    await goToAudit();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/audit/integrity/verify'))
        return new Response(
          JSON.stringify({ verified: true, verified_events: 42, chain_tip_hash: 'abcdef1234567890' }),
          { status: 200 }
        );
      if (url.includes('/governance/audit')) return new Response(JSON.stringify([AUDIT_EVENT]), { status: 200 });
      return new Response(JSON.stringify([]), { status: 200 });
    });
    await userEvent.click(screen.getByText('Verify chain'));
    await waitFor(() => expect(screen.getByText(/42 events verified/)).toBeInTheDocument());
    expect(screen.getByText('Chain verified')).toBeInTheDocument();
  });

  test('reports a broken audit chain', async () => {
    await goToAudit();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/audit/integrity/verify'))
        return new Response(
          JSON.stringify({ verified: false, verified_events: 10, broken_chain_at: 'evt-005' }),
          { status: 200 }
        );
      if (url.includes('/governance/audit')) return new Response(JSON.stringify([AUDIT_EVENT]), { status: 200 });
      return new Response(JSON.stringify([]), { status: 200 });
    });
    await userEvent.click(screen.getByText('Verify chain'));
    await waitFor(() => expect(screen.getByText('Chain broken!')).toBeInTheDocument());
    expect(screen.getByText(/Hash chain broken at event evt-005/)).toBeInTheDocument();
  });

  test('shows a toast when chain verification request fails', async () => {
    await goToAudit();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/audit/integrity/verify')) return new Response(null, { status: 500 });
      if (url.includes('/governance/audit')) return new Response(JSON.stringify([AUDIT_EVENT]), { status: 200 });
      return new Response(JSON.stringify([]), { status: 200 });
    });
    await userEvent.click(screen.getByText('Verify chain'));
    await waitFor(() =>
      expect(
        useToastStore.getState().toasts.some((t) => t.message === 'Chain verification failed (requires database).')
      ).toBe(true)
    );
  });

  test('opens the event detail drawer on row click and closes it', async () => {
    await goToAudit();
    await userEvent.click(screen.getByTestId('audit-row'));
    expect(screen.getByText('Event Details')).toBeInTheDocument();
    expect(screen.getByText('event_id')).toBeInTheDocument();
    const closeBtn = screen.getByText('Event Details').parentElement!.querySelector('button')!;
    await userEvent.click(closeBtn);
    expect(screen.queryByText('Event Details')).not.toBeInTheDocument();
  });

  test('closes the event detail drawer by clicking the backdrop', async () => {
    await goToAudit();
    await userEvent.click(screen.getByTestId('audit-row'));
    expect(screen.getByText('Event Details')).toBeInTheDocument();
    const backdrop = screen.getByText('Event Details').closest('div.fixed')!;
    await userEvent.click(backdrop);
    expect(screen.queryByText('Event Details')).not.toBeInTheDocument();
  });

  test('exports audit events as JSON', async () => {
    await goToAudit();
    const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:json-url');
    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    await userEvent.click(screen.getByTestId('export-json-btn'));
    expect(createObjectURL).toHaveBeenCalled();
    expect(clickSpy).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:json-url');
  });

  test('exports audit events as CSV', async () => {
    await goToAudit();
    const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:csv-url');
    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    await userEvent.click(screen.getByTestId('export-csv-btn'));
    expect(createObjectURL).toHaveBeenCalled();
    expect(clickSpy).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:csv-url');
  });

  test('navigating to a goal from the audit row does not open the detail drawer', async () => {
    await goToAudit();
    await userEvent.click(screen.getByText(/goal-xyz/));
    expect(screen.queryByText('Event Details')).not.toBeInTheDocument();
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// BUDGET TAB — advanced flows
// ═══════════════════════════════════════════════════════════════════════════════

describe('GovernancePage — Budget tab advanced', () => {
  const ANOMALY = {
    id: 'anomaly-1',
    type: 'spend_spike',
    message: 'Spend spiked 3x above baseline',
    severity: 'high',
    cost_delta_usd: 12.5,
    detected_at: new Date().toISOString(),
  };

  test('renders cost anomalies list when anomalies are present', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/governance/budget')) return new Response(JSON.stringify(BUDGET), { status: 200 });
      if (url.includes('/costs/anomalies')) return new Response(JSON.stringify([ANOMALY]), { status: 200 });
      if (url.includes('/costs/summary'))
        return new Response(JSON.stringify({ total_cost_usd: 10, total_goals: 4 }), { status: 200 });
      return new Response(JSON.stringify([]), { status: 200 });
    });
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('tab-budget'));
    await waitFor(() => expect(screen.getByText('Cost Anomalies')).toBeInTheDocument());
    expect(screen.getByText('spend_spike')).toBeInTheDocument();
    expect(screen.getByText('Spend spiked 3x above baseline')).toBeInTheDocument();
    expect(screen.getByText('high')).toBeInTheDocument();
    expect(screen.getByText('+$12.50')).toBeInTheDocument();
  });

  test('shows the near-limit warning when spend exceeds 80% of the daily budget', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/governance/budget'))
        return new Response(JSON.stringify({ tenant_id: 't', per_goal_usd: 10, per_tenant_daily_usd: 100 }), { status: 200 });
      if (url.includes('/costs/summary'))
        return new Response(JSON.stringify({ total_cost_usd: 90, total_goals: 3 }), { status: 200 });
      if (url.includes('/costs/anomalies')) return new Response(JSON.stringify([]), { status: 200 });
      return new Response(JSON.stringify([]), { status: 200 });
    });
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('tab-budget'));
    await waitFor(() => expect(screen.getByText(/Near limit/)).toBeInTheDocument());
  });

  test('shows quick links to the cost dashboard and budget manager', async () => {
    await (async () => {
      mockFetch({ budget: BUDGET });
      renderGovernancePage();
      await userEvent.click(screen.getByTestId('tab-budget'));
    })();
    await waitFor(() => expect(screen.getByText('Full Cost Dashboard')).toBeInTheDocument());
    expect(screen.getByText('Advanced Budget Manager')).toBeInTheDocument();
  });

  test('shows a failure toast when saving the budget fails', async () => {
    mockFetch({ budget: BUDGET });
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('tab-budget'));
    await waitFor(() => expect(screen.getByLabelText(/per-goal limit/i)).toBeInTheDocument());
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/governance/budget') && init?.method === 'PUT') return new Response(null, { status: 500 });
      if (url.includes('/governance/budget')) return new Response(JSON.stringify(BUDGET), { status: 200 });
      return new Response(JSON.stringify([]), { status: 200 });
    });
    await userEvent.clear(screen.getByLabelText(/per-goal limit/i));
    await userEvent.type(screen.getByLabelText(/per-goal limit/i), '25');
    await userEvent.click(screen.getByTestId('save-budget-btn'));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.message === 'Failed to save budget.')).toBe(true)
    );
  });

  test('changes the per-tenant daily limit input', async () => {
    mockFetch({ budget: BUDGET });
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('tab-budget'));
    await waitFor(() => expect(screen.getByLabelText(/daily tenant limit/i)).toBeInTheDocument());
    await userEvent.clear(screen.getByLabelText(/daily tenant limit/i));
    await userEvent.type(screen.getByLabelText(/daily tenant limit/i), '900');
    await waitFor(() => expect(screen.getByTestId('save-budget-btn')).toBeInTheDocument());
    expect(screen.getByLabelText(/daily tenant limit/i)).toHaveValue(900);
  });

  test('shows loading spinners while the budget is loading', async () => {
    let resolveBudget: (r: Response) => void;
    const pendingBudget = new Promise<Response>((res) => { resolveBudget = res; });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/governance/budget')) return pendingBudget;
      return new Response(JSON.stringify([]), { status: 200 });
    });
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('tab-budget'));
    expect(screen.getByText('Budget Limits')).toBeInTheDocument();
    resolveBudget!(new Response(JSON.stringify(BUDGET), { status: 200 }));
    await waitFor(() => expect(screen.getByLabelText(/per-goal limit/i)).toBeInTheDocument());
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// MAIN PAGE
// ═══════════════════════════════════════════════════════════════════════════════

describe('GovernancePage — main page', () => {
  test('shows a pending-count badge on the Approvals tab', async () => {
    mockFetch({ approvals: [APPROVAL] });
    renderGovernancePage();
    await waitFor(() => {
      const tab = screen.getByTestId('tab-approvals');
      expect(within(tab).getByText('1')).toBeInTheDocument();
    });
  });

  test('does not show a badge on the Approvals tab when there are no pending approvals', async () => {
    mockFetch({ approvals: [] });
    renderGovernancePage();
    await waitFor(() => expect(screen.getByTestId('tab-approvals')).toBeInTheDocument());
    const tab = screen.getByTestId('tab-approvals');
    expect(within(tab).queryByText('0')).not.toBeInTheDocument();
  });

  test('switches between tabs and renders the corresponding panel content', async () => {
    mockFetch();
    renderGovernancePage();
    await userEvent.click(screen.getByTestId('tab-audit'));
    await waitFor(() => expect(screen.getByTestId('audit-filters')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('tab-budget'));
    await waitFor(() => expect(screen.getByText('Budget Limits')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('tab-policies'));
    await waitFor(() => expect(screen.getByTestId('policies-empty')).toBeInTheDocument());
  });
});
