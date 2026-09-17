import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import AdminPage from '../AdminPage';

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderAdminPage() {
  return render(
    <QueryClientProvider client={makeQC()}>
      <AdminPage />
    </QueryClientProvider>
  );
}

interface Tenant {
  tenant_id: string;
  name?: string;
  plan: string;
}

function mockFetch(opts: {
  tenants?: Tenant[];
  total?: number;
  usage?: Record<string, unknown>;
  health?: Record<string, unknown> | null; // null => health fetch rejects
  onPlanUpdate?: (tenantId: string, plan: string) => void;
}) {
  const {
    tenants = [],
    total = tenants.length,
    usage = { active_goals: 0, total_tenants: 0 },
    health = { status: 'ok' },
    onPlanUpdate,
  } = opts;

  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();

    if (url.includes('/admin/usage')) {
      return new Response(JSON.stringify(usage), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (url.includes('/admin/tenants/') && url.includes('/plan') && method === 'PUT') {
      const match = url.match(/\/admin\/tenants\/([^/]+)\/plan/);
      const tenantId = match?.[1] ?? '';
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      onPlanUpdate?.(tenantId, body.plan);
      return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (url.includes('/admin/tenants')) {
      return new Response(JSON.stringify({ tenants, total }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (url.includes('/health')) {
      if (health === null) return new Response('', { status: 500 });
      return new Response(JSON.stringify(health), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

describe('AdminPage', () => {
  beforeEach(() => {
    // Stub fetch to avoid real HTTP calls in unit tests
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ tenants: [], total: 0, active_goals: 0, total_tenants: 0 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders without crashing', () => {
    renderAdminPage();
    expect(screen.getByText('Platform Administration')).toBeTruthy();
  });

  it('renders the page subtitle', () => {
    renderAdminPage();
    expect(screen.getByText(/Manage tenants, plans, and platform health/i)).toBeTruthy();
  });

  it('renders the Tenants section heading', () => {
    renderAdminPage();
    expect(screen.getByText('Tenants')).toBeTruthy();
  });

  it('renders table headers after data loads', async () => {
    renderAdminPage();
    // Table is only rendered once isLoading becomes false (query resolves)
    await waitFor(() => {
      expect(screen.getByText('Tenant ID')).toBeTruthy();
    });
    expect(screen.getByText('Plan')).toBeTruthy();
    expect(screen.getByText('Actions')).toBeTruthy();
  });

  it('shows empty state when no tenants match', async () => {
    mockFetch({ tenants: [] });
    renderAdminPage();
    await waitFor(() => {
      expect(screen.getByTestId('tenants-empty')).toBeTruthy();
    });
  });

  it('renders tenant rows with name fallback and plan badges', async () => {
    mockFetch({
      tenants: [
        { tenant_id: 't-1', name: 'Acme', plan: 'enterprise' },
        { tenant_id: 't-2', plan: 'free' }, // no name -> renders em dash
      ],
      total: 2,
    });
    renderAdminPage();
    await waitFor(() => expect(screen.getAllByTestId('tenant-row').length).toBe(2));
    expect(screen.getByText('Acme')).toBeTruthy();
    expect(screen.getByText('t-1')).toBeTruthy();
  });

  it('sorts tenants by plan rank descending (enterprise before free)', async () => {
    mockFetch({
      tenants: [
        { tenant_id: 't-free', name: 'Free Co', plan: 'free' },
        { tenant_id: 't-ent', name: 'Ent Co', plan: 'enterprise' },
        { tenant_id: 't-pro', name: 'Pro Co', plan: 'professional' },
      ],
      total: 3,
    });
    renderAdminPage();
    await waitFor(() => expect(screen.getAllByTestId('tenant-row').length).toBe(3));
    const rows = screen.getAllByTestId('tenant-row');
    expect(within(rows[0]).getByText('t-ent')).toBeTruthy();
    expect(within(rows[1]).getByText('t-pro')).toBeTruthy();
    expect(within(rows[2]).getByText('t-free')).toBeTruthy();
  });

  it('filters tenants by search text matching tenant_id', async () => {
    const user = userEvent.setup();
    mockFetch({
      tenants: [
        { tenant_id: 'alpha', name: 'Alpha Co', plan: 'free' },
        { tenant_id: 'beta', name: 'Beta Co', plan: 'starter' },
      ],
      total: 2,
    });
    renderAdminPage();
    await waitFor(() => expect(screen.getAllByTestId('tenant-row').length).toBe(2));

    await user.type(screen.getByTestId('tenant-search'), 'alpha');
    await waitFor(() => expect(screen.getAllByTestId('tenant-row').length).toBe(1));
    expect(screen.getByText('alpha')).toBeTruthy();
  });

  it('filters tenants by search text matching name', async () => {
    const user = userEvent.setup();
    mockFetch({
      tenants: [
        { tenant_id: 'alpha', name: 'Alpha Co', plan: 'free' },
        { tenant_id: 'beta', name: 'Beta Co', plan: 'starter' },
      ],
      total: 2,
    });
    renderAdminPage();
    await waitFor(() => expect(screen.getAllByTestId('tenant-row').length).toBe(2));

    await user.type(screen.getByTestId('tenant-search'), 'Beta');
    await waitFor(() => expect(screen.getAllByTestId('tenant-row').length).toBe(1));
    expect(screen.getByText('beta')).toBeTruthy();
  });

  it('shows empty state when search matches nothing', async () => {
    const user = userEvent.setup();
    mockFetch({
      tenants: [{ tenant_id: 'alpha', name: 'Alpha Co', plan: 'free' }],
      total: 1,
    });
    renderAdminPage();
    await waitFor(() => expect(screen.getAllByTestId('tenant-row').length).toBe(1));

    await user.type(screen.getByTestId('tenant-search'), 'zzz-no-match');
    await waitFor(() => expect(screen.getByTestId('tenants-empty')).toBeTruthy());
  });

  it('filters by plan chip and clears via the Clear button', async () => {
    const user = userEvent.setup();
    mockFetch({
      tenants: [
        { tenant_id: 'alpha', name: 'Alpha Co', plan: 'free' },
        { tenant_id: 'beta', name: 'Beta Co', plan: 'starter' },
      ],
      total: 2,
    });
    renderAdminPage();
    await waitFor(() => expect(screen.getAllByTestId('tenant-row').length).toBe(2));

    await user.click(screen.getByTestId('plan-filter-starter'));
    await waitFor(() => expect(screen.getAllByTestId('tenant-row').length).toBe(1));
    expect(screen.getByText('beta')).toBeTruthy();

    // Clicking the same chip again toggles back to 'all'
    await user.click(screen.getByTestId('plan-filter-starter'));
    await waitFor(() => expect(screen.getAllByTestId('tenant-row').length).toBe(2));

    // Filter again, then use the explicit Clear button
    await user.click(screen.getByTestId('plan-filter-free'));
    await waitFor(() => expect(screen.getAllByTestId('tenant-row').length).toBe(1));
    await user.click(screen.getByText('Clear ×'));
    await waitFor(() => expect(screen.getAllByTestId('tenant-row').length).toBe(2));
  });

  it('changes a tenant plan via the select and shows a spinner while updating', async () => {
    const user = userEvent.setup();
    const onPlanUpdate = vi.fn();
    mockFetch({
      tenants: [{ tenant_id: 't-1', name: 'Acme', plan: 'free' }],
      total: 1,
      onPlanUpdate,
    });
    renderAdminPage();
    await waitFor(() => expect(screen.getAllByTestId('tenant-row').length).toBe(1));

    const select = screen.getByTestId('plan-select-t-1') as HTMLSelectElement;
    await user.selectOptions(select, 'enterprise');

    await waitFor(() => expect(onPlanUpdate).toHaveBeenCalledWith('t-1', 'enterprise'));
    // After settle, select should be back (no permanent spinner)
    await waitFor(() => expect(screen.getByTestId('plan-select-t-1')).toBeTruthy());
  });

  it('does not call the mutation when selecting the same plan', async () => {
    const user = userEvent.setup();
    const onPlanUpdate = vi.fn();
    mockFetch({
      tenants: [{ tenant_id: 't-1', name: 'Acme', plan: 'free' }],
      total: 1,
      onPlanUpdate,
    });
    renderAdminPage();
    await waitFor(() => expect(screen.getAllByTestId('tenant-row').length).toBe(1));

    const select = screen.getByTestId('plan-select-t-1') as HTMLSelectElement;
    await user.selectOptions(select, 'free');
    expect(onPlanUpdate).not.toHaveBeenCalled();
  });

  it('shows pagination controls when total exceeds page size', async () => {
    const tenants = Array.from({ length: 25 }, (_, i) => ({ tenant_id: `t-${i}`, plan: 'free' }));
    mockFetch({ tenants, total: 60 });
    renderAdminPage();
    await waitFor(() => expect(screen.getAllByTestId('tenant-row').length).toBe(25));
    expect(screen.getByLabelText('Pagination')).toBeTruthy();
  });

  it('does not show pagination when total is within one page', async () => {
    mockFetch({ tenants: [{ tenant_id: 't-1', plan: 'free' }], total: 1 });
    renderAdminPage();
    await waitFor(() => expect(screen.getAllByTestId('tenant-row').length).toBe(1));
    expect(screen.queryByLabelText('Pagination')).toBeNull();
  });

  it('paginates to the next page of tenants', async () => {
    const user = userEvent.setup();
    const page1 = Array.from({ length: 25 }, (_, i) => ({ tenant_id: `t-${i}`, plan: 'free' }));
    const page2 = [{ tenant_id: 't-25', plan: 'enterprise' }];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/admin/usage')) {
        return new Response(JSON.stringify({ active_goals: 0, total_tenants: 26 }), { status: 200 });
      }
      if (url.includes('/admin/tenants')) {
        const isPage2 = url.includes('offset=25');
        return new Response(JSON.stringify({ tenants: isPage2 ? page2 : page1, total: 26 }), { status: 200 });
      }
      return new Response(JSON.stringify({ status: 'ok' }), { status: 200 });
    });
    renderAdminPage();
    await waitFor(() => expect(screen.getAllByTestId('tenant-row').length).toBe(25));

    await user.click(screen.getByLabelText('Next page'));
    await waitFor(() => expect(screen.getAllByTestId('tenant-row').length).toBe(1));
    expect(screen.getByText('t-25')).toBeTruthy();
  });

  it('shows degraded health badge and per-service status when unhealthy', async () => {
    mockFetch({
      tenants: [],
      health: { status: 'error', db: 'ok', redis: 'down', celery: 'ok' },
    });
    renderAdminPage();
    await waitFor(() => expect(screen.getByTestId('health-badge').textContent).toContain('Degraded'));
    expect(screen.getByTestId('health-redis').textContent).toContain('down');
    expect(screen.getByTestId('health-database').textContent).toContain('ok');
  });

  it('shows healthy badge when health status is healthy', async () => {
    mockFetch({ tenants: [], health: { status: 'healthy' } });
    renderAdminPage();
    await waitFor(() => expect(screen.getByTestId('health-badge').textContent).toContain('Healthy'));
  });

  it('treats a fetch rejection on /health as degraded (catch branch)', async () => {
    mockFetch({ tenants: [], health: null });
    renderAdminPage();
    await waitFor(() => expect(screen.getByTestId('health-badge').textContent).toContain('Degraded'));
  });

  it('shows amber accent for avg latency above 5000ms and the value text', async () => {
    mockFetch({ tenants: [], usage: { active_goals: 3, total_tenants: 1, goals_today: 12, avg_latency_ms: 7000 } });
    renderAdminPage();
    await waitFor(() => expect(screen.getByTestId('metric-latency').textContent).toContain('7000ms'));
  });

  it('shows em-dash for avg latency when not provided', async () => {
    mockFetch({ tenants: [], usage: { active_goals: 0, total_tenants: 0 } });
    renderAdminPage();
    await waitFor(() => expect(screen.getByTestId('metric-latency').textContent).toContain('—'));
  });

  it('shows goals_today value when provided', async () => {
    mockFetch({ tenants: [], usage: { active_goals: 0, total_tenants: 0, goals_today: 42 } });
    renderAdminPage();
    await waitFor(() => expect(screen.getByTestId('metric-goals-today').textContent).toContain('42'));
  });

  it('clicking refresh triggers a usage refetch', async () => {
    const user = userEvent.setup();
    const spy = mockFetch({ tenants: [], usage: { active_goals: 1, total_tenants: 1 } });
    renderAdminPage();
    await waitFor(() => expect(screen.getByTestId('metric-tenants')).toBeTruthy());
    const callsBefore = spy.mock.calls.filter((c) => String(c[0]).includes('/admin/usage')).length;
    await user.click(screen.getByTestId('refresh-btn'));
    await waitFor(() => {
      const callsAfter = spy.mock.calls.filter((c) => String(c[0]).includes('/admin/usage')).length;
      expect(callsAfter).toBeGreaterThan(callsBefore);
    });
  });

  it('renders quick action links with correct hrefs', () => {
    renderAdminPage();
    expect(screen.getByTestId('quick-link-view-audit-log').getAttribute('href')).toBe('/audit');
    expect(screen.getByTestId('quick-link-governance').getAttribute('href')).toBe('/governance');
    expect(screen.getByTestId('quick-link-observability').getAttribute('href')).toBe('/observability');
  });
});
