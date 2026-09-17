import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import React, { type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { RbacPage } from './RbacPage';

// ConfirmModal (rendered whenever a delete flow is exercised) uses
// framer-motion's AnimatePresence. jsdom has no real rAF/animation
// completion, so an "exit" animation can leave the panel in the DOM (or
// remove it before assertions run). Stub framer-motion the same way sibling
// feature tests in this repo do: a memoized per-tag stub (not a fresh
// component per render, which would remount and lose state on every re-render).
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const stubCache = new Map<string, (props: { children?: ReactNode; [k: string]: unknown }) => React.ReactElement>();
  const makeStub = (tag: string) => {
    let stub = stubCache.get(tag);
    if (!stub) {
      stub = ({ children, ...props }) => React.createElement(tag, props as Record<string, unknown>, children);
      stubCache.set(tag, stub);
    }
    return stub;
  };
  return {
    ...actual,
    useReducedMotion: () => true,
    AnimatePresence: ({ children }: { children?: ReactNode }) => <>{children}</>,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : makeStub(key)),
    }),
  };
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><RbacPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

function role(id: string, user_id: string, roleStr: string, created_at = '2024-01-05T00:00:00Z') {
  return { id, user_id, role: roleStr, created_at };
}

function ip(id: string, cidr: string, description = '', created_at = '2024-01-05T00:00:00Z') {
  return { id, cidr, description, created_at };
}

function mockFetch(
  roles: unknown[] = [],
  ips: unknown[] = [],
  opts: { failRoleDelete?: boolean; failIpAdd?: boolean; failGrant?: boolean } = {},
) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init as RequestInit | undefined)?.method ?? 'GET';

    if (url.includes('/tenants/me/roles') && method === 'GET')
      return new Response(JSON.stringify(roles), { status: 200, headers: { 'Content-Type': 'application/json' } });

    if (url.includes('/tenants/me/roles') && method === 'POST') {
      if (opts.failGrant) return new Response(JSON.stringify({ detail: 'grant failed' }), { status: 500, headers: { 'Content-Type': 'application/json' } });
      return new Response(JSON.stringify({ id: 'r-new', user_id: 'bob', role: 'viewer' }), { status: 201, headers: { 'Content-Type': 'application/json' } });
    }

    if (url.includes('/tenants/me/roles/') && method === 'DELETE') {
      if (opts.failRoleDelete) return new Response(JSON.stringify({ detail: 'delete failed' }), { status: 500, headers: { 'Content-Type': 'application/json' } });
      return new Response(null, { status: 204 });
    }

    if (url.includes('/tenants/me/ip-allowlist') && method === 'GET')
      return new Response(JSON.stringify(ips), { status: 200, headers: { 'Content-Type': 'application/json' } });

    if (url.includes('/tenants/me/ip-allowlist') && method === 'POST') {
      if (opts.failIpAdd) return new Response(JSON.stringify({ detail: 'add failed' }), { status: 500, headers: { 'Content-Type': 'application/json' } });
      return new Response(JSON.stringify({ id: 'e-new', cidr: '192.168.0.0/16', description: '' }), { status: 201, headers: { 'Content-Type': 'application/json' } });
    }

    if (url.includes('/tenants/me/ip-allowlist/') && method === 'DELETE')
      return new Response(null, { status: 204 });

    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
  useToastStore.setState({ toasts: [] });
});
afterEach(() => vi.restoreAllMocks());

describe('RbacPage', () => {
  test('renders Access Control heading', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByRole('heading', { name: /access control/i })).toBeInTheDocument();
  });

  test('lists role assignments', async () => {
    mockFetch([role('r1', 'alice', 'approver')]);
    renderPage();
    expect(await screen.findByText('alice')).toBeInTheDocument();
  });

  test('shows grant role button', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('grant-role-btn')).toBeInTheDocument());
  });

  test('lists IP allowlist entries', async () => {
    mockFetch([], [ip('e1', '10.0.0.0/8', 'office')]);
    renderPage();
    await waitFor(() => expect(screen.getByTestId('tab-ip')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('tab-ip'));
    expect(await screen.findByText('10.0.0.0/8')).toBeInTheDocument();
  });

  test('add IP sends POST to ip-allowlist', async () => {
    const spy = mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('tab-ip')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('tab-ip'));
    await waitFor(() => expect(screen.getByTestId('add-cidr-btn')).toBeInTheDocument());
    const cidrInput = screen.getByPlaceholderText(/10\.0\.0\.0|CIDR/i);
    await userEvent.type(cidrInput, '192.168.0.0/16');
    await userEvent.click(screen.getByTestId('add-cidr-btn'));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/tenants/me/ip-allowlist') && (i as RequestInit)?.method === 'POST'
      )).toBe(true)
    );
  });

  // ── Empty states ─────────────────────────────────────────────────────────

  test('shows empty state for role assignments with no search', async () => {
    mockFetch([]);
    renderPage();
    expect(await screen.findByText('No role assignments yet')).toBeInTheDocument();
    expect(screen.getByText('Grant a role to give a team member access.')).toBeInTheDocument();
  });

  test('shows empty state for IP allowlist', async () => {
    mockFetch([], []);
    renderPage();
    await userEvent.click(screen.getByTestId('tab-ip'));
    expect(await screen.findByText('No allowlist entries')).toBeInTheDocument();
  });

  // ── Search ───────────────────────────────────────────────────────────────

  test('search filters roles by user_id and clears selection', async () => {
    mockFetch([role('r1', 'alice', 'approver'), role('r2', 'bob', 'viewer')]);
    renderPage();
    await screen.findByText('alice');

    // Select all first
    await userEvent.click(screen.getByLabelText('Select all'));
    expect(await screen.findByText(/Remove 2 selected/)).toBeInTheDocument();

    const search = screen.getByPlaceholderText(/Search by user or role/i);
    await userEvent.type(search, 'alice');

    expect(screen.getByText('alice')).toBeInTheDocument();
    expect(screen.queryByText('bob')).not.toBeInTheDocument();
    // Selection cleared by typing in search
    expect(screen.queryByText(/selected/)).not.toBeInTheDocument();
  });

  test('search filters roles by role name', async () => {
    mockFetch([role('r1', 'alice', 'approver'), role('r2', 'bob', 'viewer')]);
    renderPage();
    await screen.findByText('alice');
    const search = screen.getByPlaceholderText(/Search by user or role/i);
    await userEvent.type(search, 'viewer');
    expect(screen.getByText('bob')).toBeInTheDocument();
    expect(screen.queryByText('alice')).not.toBeInTheDocument();
  });

  test('shows "No matching assignments" empty state when search has no results', async () => {
    mockFetch([role('r1', 'alice', 'approver')]);
    renderPage();
    await screen.findByText('alice');
    const search = screen.getByPlaceholderText(/Search by user or role/i);
    await userEvent.type(search, 'zzz-nomatch');
    expect(await screen.findByText('No matching assignments')).toBeInTheDocument();
    expect(screen.getByText('Try a different search term.')).toBeInTheDocument();
  });

  // ── Selection / bulk delete ──────────────────────────────────────────────

  test('selects a single row, then select-all/indeterminate, then bulk deletes', async () => {
    const spy = mockFetch([role('r1', 'alice', 'approver'), role('r2', 'bob', 'viewer')]);
    renderPage();
    await screen.findByText('alice');

    await userEvent.click(screen.getByLabelText('Select alice'));
    expect(await screen.findByText(/Remove 1 selected/)).toBeInTheDocument();

    const selectAll = screen.getByLabelText('Select all') as HTMLInputElement;
    expect(selectAll.indeterminate).toBe(true);

    await userEvent.click(selectAll);
    expect(await screen.findByText(/Remove 2 selected/)).toBeInTheDocument();

    // Toggling all off again
    await userEvent.click(selectAll);
    expect(screen.queryByText(/selected/)).not.toBeInTheDocument();

    // Re-select all and confirm bulk delete
    await userEvent.click(selectAll);
    await userEvent.click(await screen.findByText(/Remove 2 selected/));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Remove 2 assignments?')).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: /^Remove 2$/ }));

    await waitFor(() =>
      expect(spy.mock.calls.filter(([u, i]) =>
        String(u).includes('/tenants/me/roles/') && (i as RequestInit)?.method === 'DELETE'
      ).length).toBe(2)
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(useToastStore.getState().toasts.some((t) => t.message.includes('2 assignments removed'))).toBe(true);
  });

  test('bulk delete failure shows error toast', async () => {
    mockFetch([role('r1', 'alice', 'approver')], [], { failRoleDelete: true });
    renderPage();
    await screen.findByText('alice');
    await userEvent.click(screen.getByLabelText('Select alice'));
    await userEvent.click(await screen.findByText(/Remove 1 selected/));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: /^Remove 1$/ }));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.kind === 'error' && t.message.includes('Bulk delete failed'))).toBe(true)
    );
  });

  test('bulk confirm modal can be cancelled', async () => {
    mockFetch([role('r1', 'alice', 'approver')]);
    renderPage();
    await screen.findByText('alice');
    await userEvent.click(screen.getByLabelText('Select alice'));
    await userEvent.click(await screen.findByText(/Remove 1 selected/));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: /^Cancel$/ }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  // ── Single-row delete ────────────────────────────────────────────────────

  test('deletes a single role assignment via row action and shows success toast', async () => {
    const spy = mockFetch([role('r1', 'alice', 'approver')]);
    renderPage();
    await screen.findByText('alice');

    await userEvent.click(screen.getByTestId('delete-role-r1'));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Remove role assignment?')).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: /^Remove$/ }));

    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/tenants/me/roles/r1') && (i as RequestInit)?.method === 'DELETE'
      )).toBe(true)
    );
    expect(useToastStore.getState().toasts.some((t) => t.message === 'Role assignment removed')).toBe(true);
  });

  test('single role delete failure shows error toast', async () => {
    mockFetch([role('r1', 'alice', 'approver')], [], { failRoleDelete: true });
    renderPage();
    await screen.findByText('alice');
    await userEvent.click(screen.getByTestId('delete-role-r1'));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: /^Remove$/ }));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.kind === 'error' && t.message.startsWith('Failed:'))).toBe(true)
    );
  });

  test('cancelling single role delete closes dialog without deleting', async () => {
    const spy = mockFetch([role('r1', 'alice', 'approver')]);
    renderPage();
    await screen.findByText('alice');
    await userEvent.click(screen.getByTestId('delete-role-r1'));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: /^Cancel$/ }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(spy.mock.calls.some(([, i]) => (i as RequestInit)?.method === 'DELETE')).toBe(false);
  });

  // ── Grant role modal ─────────────────────────────────────────────────────

  test('opens grant role modal, selects a role, sets condition, and submits', async () => {
    const spy = mockFetch([]);
    renderPage();
    await screen.findByText('No role assignments yet');

    await userEvent.click(screen.getByTestId('grant-role-btn'));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('heading', { name: 'Grant Role' })).toBeInTheDocument();

    const submitBtn = within(dialog).getByRole('button', { name: /Grant Role/i });
    expect(submitBtn).toBeDisabled();

    await userEvent.type(within(dialog).getByLabelText(/User ID or email/i), 'newuser@example.com');
    expect(submitBtn).toBeEnabled();

    await userEvent.click(within(dialog).getByRole('button', { name: /^operator/ }));
    await userEvent.type(within(dialog).getByLabelText(/Condition/i), 'agent_id=abc');

    await userEvent.click(submitBtn);

    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => {
        if (!String(u).includes('/tenants/me/roles') || (i as RequestInit)?.method !== 'POST') return false;
        const body = JSON.parse(String((i as RequestInit).body));
        return body.role === 'operator:agent_id=abc';
      })).toBe(true)
    );
    expect(useToastStore.getState().toasts.some((t) => t.message === 'Role assigned successfully')).toBe(true);
    // Modal closes and fields reset on success
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  test('grant role modal can be closed via cancel and by backdrop click', async () => {
    mockFetch([]);
    renderPage();
    await userEvent.click(screen.getByTestId('grant-role-btn'));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  test('grant role failure shows error toast', async () => {
    mockFetch([], [], { failGrant: true });
    renderPage();
    await userEvent.click(screen.getByTestId('grant-role-btn'));
    const dialog = await screen.findByRole('dialog');
    await userEvent.type(within(dialog).getByLabelText(/User ID or email/i), 'x@example.com');
    await userEvent.click(within(dialog).getByRole('button', { name: /Grant Role/i }));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.kind === 'error' && t.message.includes('Failed to assign role'))).toBe(true)
    );
  });

  // ── IP allowlist: validation, delete, description fallback ──────────────

  test('invalid CIDR shows validation error and does not call API', async () => {
    const spy = mockFetch();
    renderPage();
    await userEvent.click(screen.getByTestId('tab-ip'));
    const cidrInput = await screen.findByLabelText('CIDR Block');
    await userEvent.type(cidrInput, 'not-a-cidr');
    await userEvent.click(screen.getByTestId('add-cidr-btn'));
    expect(await screen.findByText(/Invalid CIDR/i)).toBeInTheDocument();
    expect(spy.mock.calls.some(([u, i]) =>
      String(u).includes('/tenants/me/ip-allowlist') && (i as RequestInit)?.method === 'POST'
    )).toBe(false);
  });

  test('CIDR error clears when input changes', async () => {
    mockFetch();
    renderPage();
    await userEvent.click(screen.getByTestId('tab-ip'));
    const cidrInput = await screen.findByLabelText('CIDR Block');
    await userEvent.type(cidrInput, 'bad');
    await userEvent.click(screen.getByTestId('add-cidr-btn'));
    expect(await screen.findByText(/Invalid CIDR/i)).toBeInTheDocument();
    await userEvent.type(cidrInput, '/8');
    expect(screen.queryByText(/Invalid CIDR/i)).not.toBeInTheDocument();
  });

  test('submitting CIDR via Enter key triggers add', async () => {
    const spy = mockFetch();
    renderPage();
    await userEvent.click(screen.getByTestId('tab-ip'));
    const cidrInput = await screen.findByLabelText('CIDR Block');
    await userEvent.type(cidrInput, '10.0.0.0/8{Enter}');
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/tenants/me/ip-allowlist') && (i as RequestInit)?.method === 'POST'
      )).toBe(true)
    );
  });

  test('submitting via Enter in description field also triggers add', async () => {
    const spy = mockFetch();
    renderPage();
    await userEvent.click(screen.getByTestId('tab-ip'));
    const cidrInput = await screen.findByLabelText('CIDR Block');
    const descInput = await screen.findByLabelText('Description');
    await userEvent.type(cidrInput, '10.0.0.0/8');
    await userEvent.type(descInput, 'office{Enter}');
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/tenants/me/ip-allowlist') && (i as RequestInit)?.method === 'POST'
      )).toBe(true)
    );
    expect(useToastStore.getState().toasts.some((t) => t.message === 'CIDR added to allowlist')).toBe(true);
  });

  test('add CIDR failure shows error toast', async () => {
    mockFetch([], [], { failIpAdd: true });
    renderPage();
    await userEvent.click(screen.getByTestId('tab-ip'));
    const cidrInput = await screen.findByLabelText('CIDR Block');
    await userEvent.type(cidrInput, '10.0.0.0/8');
    await userEvent.click(screen.getByTestId('add-cidr-btn'));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.kind === 'error' && t.message.includes('Failed to add CIDR'))).toBe(true)
    );
  });

  test('IP entry without a description shows fallback text', async () => {
    mockFetch([], [ip('e1', '172.16.0.0/12', '')]);
    renderPage();
    await userEvent.click(screen.getByTestId('tab-ip'));
    expect(await screen.findByText('No description')).toBeInTheDocument();
  });

  test('deletes an IP allowlist entry via row action', async () => {
    const spy = mockFetch([], [ip('e1', '10.0.0.0/8', 'office')]);
    renderPage();
    await userEvent.click(screen.getByTestId('tab-ip'));
    await screen.findByText('10.0.0.0/8');

    await userEvent.click(screen.getByTestId('delete-ip-e1'));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Remove CIDR entry?')).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: /^Remove$/ }));

    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/tenants/me/ip-allowlist/e1') && (i as RequestInit)?.method === 'DELETE'
      )).toBe(true)
    );
    expect(useToastStore.getState().toasts.some((t) => t.message === 'CIDR entry removed')).toBe(true);
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  test('cancelling IP delete keeps the entry', async () => {
    mockFetch([], [ip('e1', '10.0.0.0/8', 'office')]);
    renderPage();
    await userEvent.click(screen.getByTestId('tab-ip'));
    await screen.findByText('10.0.0.0/8');
    await userEvent.click(screen.getByTestId('delete-ip-e1'));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: /^Cancel$/ }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByText('10.0.0.0/8')).toBeInTheDocument();
  });

  // ── Role hierarchy tree ──────────────────────────────────────────────────

  test('role hierarchy tree expands/collapses child nodes', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('Role Hierarchy');

    // admin root is expanded by default, revealing approver + operator
    const approverToggle = screen.getByRole('button', { name: /approver/i });
    expect(approverToggle).toHaveAttribute('aria-expanded', 'false');

    // Expand approver -> reveals nested viewer
    await userEvent.click(approverToggle);
    expect(approverToggle).toHaveAttribute('aria-expanded', 'true');

    // Collapse it again
    await userEvent.click(approverToggle);
    expect(approverToggle).toHaveAttribute('aria-expanded', 'false');
  });

  // ── Tab switching / counts ───────────────────────────────────────────────

  test('shows counts on tabs once data has loaded', async () => {
    mockFetch(
      [role('r1', 'alice', 'approver'), role('r2', 'bob', 'viewer')],
      [ip('e1', '10.0.0.0/8', 'office')],
    );
    renderPage();
    await screen.findByText('alice');
    const rolesTab = screen.getByTestId('tab-roles');
    expect(within(rolesTab).getByText('2')).toBeInTheDocument();

    await userEvent.click(screen.getByTestId('tab-ip'));
    const ipTab = screen.getByTestId('tab-ip');
    await waitFor(() => expect(within(ipTab).getByText('1')).toBeInTheDocument());
  });

  test('grant role button only shown on roles tab', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('grant-role-btn')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('tab-ip'));
    expect(screen.queryByTestId('grant-role-btn')).not.toBeInTheDocument();
    await userEvent.click(screen.getByTestId('tab-roles'));
    expect(screen.getByTestId('grant-role-btn')).toBeInTheDocument();
  });

  test('role date is formatted, falling back to em dash when absent', async () => {
    mockFetch([role('r1', 'alice', 'approver', ''), role('r2', 'bob', 'viewer', '2024-03-15T00:00:00Z')]);
    renderPage();
    await screen.findByText('alice');
    // fmtDate falls back to em dash for falsy created_at
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
    expect(screen.getByText('Mar 15, 2024')).toBeInTheDocument();
  });
});
