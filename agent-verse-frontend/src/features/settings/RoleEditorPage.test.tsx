import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { RoleEditorPage } from './RoleEditorPage';

const CUSTOM_ROLE = {
  id: 'role-fin',
  name: 'Finance Approver',
  description: 'Approves spend',
  isBuiltIn: false,
  memberCount: 2,
  permissions: [
    { feature: 'Billing', view: true, edit: true, delete: false },
    { feature: 'Missions', view: true, edit: false, delete: false },
  ],
};

function mockFetch(customRoles: object[] = []) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/roles') && method === 'POST')
      return new Response(JSON.stringify({ id: 'role-new', ...JSON.parse(String(init?.body ?? '{}')), isBuiltIn: false }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/roles'))
      return new Response(JSON.stringify(customRoles), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <RoleEditorPage orgId="org-1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('RoleEditorPage', () => {
  test('renders the header and the five built-in roles', () => {
    mockFetch();
    renderPage();
    expect(screen.getByRole('heading', { name: /Roles & Permissions/i })).toBeInTheDocument();
    expect(screen.getByText('Org Owner')).toBeInTheDocument();
    expect(screen.getByText('Mission Lead')).toBeInTheDocument();
    expect(screen.getByText('Observer')).toBeInTheDocument();
    expect(screen.getByText(/5 built-in · 0 custom/i)).toBeInTheDocument();
  });

  test('shows the empty-state hint when there are no custom roles', async () => {
    mockFetch([]);
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/No custom roles yet/i)).toBeInTheDocument(),
    );
  });

  test('renders custom roles returned by the API', async () => {
    mockFetch([CUSTOM_ROLE]);
    renderPage();
    expect(await screen.findByText('Finance Approver')).toBeInTheDocument();
    expect(screen.getByText('2 members')).toBeInTheDocument();
    expect(screen.getByText(/5 built-in · 1 custom/i)).toBeInTheDocument();
  });

  test('expanding a role reveals its permission matrix', async () => {
    mockFetch();
    renderPage();
    // The first built-in role is Org Owner; expand its permission table.
    await userEvent.click(screen.getAllByRole('button', { name: /expand permissions/i })[0]);
    expect(
      await screen.findByRole('table', { name: /Permissions for Org Owner/i }),
    ).toBeInTheDocument();
  });

  test('New Role opens the modal; Save is gated on a name then POSTs the role', async () => {
    const spy = mockFetch();
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /create new role/i }));

    expect(await screen.findByRole('heading', { name: /New Custom Role/i })).toBeInTheDocument();
    const saveBtn = screen.getByRole('button', { name: /save role/i });
    expect(saveBtn).toBeDisabled();

    await userEvent.type(screen.getByLabelText(/Role Name/i), 'Auditor');
    expect(saveBtn).toBeEnabled();

    await userEvent.click(saveBtn);
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            String(u).includes('/v1/org/org-1/roles') &&
            (i as RequestInit)?.method === 'POST' &&
            String((i as RequestInit)?.body ?? '').includes('Auditor'),
        ),
      ).toBe(true),
    );
  });
});
