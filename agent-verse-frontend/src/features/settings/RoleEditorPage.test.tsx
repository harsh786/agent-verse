import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React, { type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { RoleEditorPage } from './RoleEditorPage';

// The New/Edit Role modal and the expandable permission matrix both use
// framer-motion's AnimatePresence. jsdom has no real rAF/animation
// completion, so an "exit" animation can leave elements in the DOM after a
// close/collapse action. Stub framer-motion the same way sibling feature
// tests in this repo do: a memoized per-tag stub (not a fresh component per
// render, which would remount and lose state on every re-render).
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
    // Saving closes the modal.
    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: /New Custom Role/i })).not.toBeInTheDocument(),
    );
  });

  test('collapsing an expanded role hides its permission matrix again', async () => {
    mockFetch();
    renderPage();
    const toggle = screen.getAllByRole('button', { name: /expand permissions/i })[0];
    await userEvent.click(toggle);
    expect(await screen.findByRole('table', { name: /Permissions for Org Owner/i })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /collapse permissions/i }));
    await waitFor(() =>
      expect(screen.queryByRole('table', { name: /Permissions for Org Owner/i })).not.toBeInTheDocument(),
    );
  });

  test('built-in roles do not expose edit or delete buttons', () => {
    mockFetch();
    renderPage();
    expect(screen.queryByRole('button', { name: /Edit Org Owner role/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Delete Org Owner role/i })).not.toBeInTheDocument();
  });

  test('cancelling the New Role modal closes it without submitting', async () => {
    const spy = mockFetch();
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /create new role/i }));
    await screen.findByRole('heading', { name: /New Custom Role/i });
    await userEvent.type(screen.getByLabelText(/Role Name/i), 'Temp');

    await userEvent.click(screen.getByRole('button', { name: /^Cancel$/i }));
    expect(screen.queryByRole('heading', { name: /New Custom Role/i })).not.toBeInTheDocument();
    expect(
      spy.mock.calls.some(([, i]) => (i as RequestInit)?.method === 'POST'),
    ).toBe(false);
  });

  test('clicking the modal backdrop closes it', async () => {
    mockFetch();
    const { container } = renderPage();
    await userEvent.click(screen.getByRole('button', { name: /create new role/i }));
    await screen.findByRole('heading', { name: /New Custom Role/i });

    const backdrop = container.querySelector('.absolute.inset-0.bg-black\\/60');
    expect(backdrop).toBeTruthy();
    await userEvent.click(backdrop as Element);
    expect(screen.queryByRole('heading', { name: /New Custom Role/i })).not.toBeInTheDocument();
  });

  test('editing a custom role pre-fills the modal and toggling a permission flips its state', async () => {
    mockFetch([CUSTOM_ROLE]);
    renderPage();
    await screen.findByText('Finance Approver');

    await userEvent.click(screen.getByRole('button', { name: /Edit Finance Approver role/i }));
    expect(await screen.findByRole('heading', { name: /Edit Role/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/Role Name/i)).toHaveValue('Finance Approver');
    expect(screen.getByLabelText(/Description/i)).toHaveValue('Approves spend');

    // Billing/view starts checked per CUSTOM_ROLE fixture; toggle it off.
    const viewBillingCheckbox = screen.getByRole('checkbox', { name: 'View Billing' });
    expect(viewBillingCheckbox).toHaveAttribute('aria-checked', 'true');
    await userEvent.click(viewBillingCheckbox);
    expect(viewBillingCheckbox).toHaveAttribute('aria-checked', 'false');

    // A never-set permission (Missions/edit) starts false; toggle it on.
    const editMissionsCheckbox = screen.getByRole('checkbox', { name: 'Edit Missions' });
    expect(editMissionsCheckbox).toHaveAttribute('aria-checked', 'false');
    await userEvent.click(editMissionsCheckbox);
    expect(editMissionsCheckbox).toHaveAttribute('aria-checked', 'true');
  });

  test('deleting a custom role calls the DELETE endpoint and announces success', async () => {
    const spy = mockFetch([CUSTOM_ROLE]);
    renderPage();
    await screen.findByText('Finance Approver');

    await userEvent.click(screen.getByRole('button', { name: /Delete Finance Approver role/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            String(u).includes('/v1/org/org-1/roles/role-fin') &&
            (i as RequestInit)?.method === 'DELETE',
        ),
      ).toBe(true),
    );
    expect(await screen.findByText('Role deleted successfully.')).toBeInTheDocument();
  });

  test('editing the description field updates its value', async () => {
    mockFetch();
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /create new role/i }));
    await screen.findByRole('heading', { name: /New Custom Role/i });
    const descInput = screen.getByLabelText(/Description/i);
    await userEvent.type(descInput, 'Handles refunds');
    expect(descInput).toHaveValue('Handles refunds');
  });
});
