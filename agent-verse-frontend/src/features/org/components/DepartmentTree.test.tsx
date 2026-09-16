import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import React, { type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import type { OrgDepartment } from '../types';

vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const makeStub = (tag: string) =>
    ({ children, ...props }: { children?: ReactNode; [k: string]: unknown }) =>
      React.createElement(tag, props as Record<string, unknown>, children);
  return {
    ...actual,
    useReducedMotion: () => true,
    AnimatePresence: ({ children }: { children?: ReactNode }) => <>{children}</>,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : makeStub(key)),
    }),
  };
});

import { DepartmentTree } from './DepartmentTree';

function dept(partial: Partial<OrgDepartment> & { id: string; name: string }): OrgDepartment {
  return {
    tenant_id: 't', org_id: 'org-1', purpose: '', capability_domains: [],
    parent_dept_id: null, manager_agent_id: null, status: 'active',
    created_at: '', updated_at: '', ...partial,
  } as OrgDepartment;
}

const DEPTS: OrgDepartment[] = [
  dept({ id: 'd1', name: 'Engineering', agent_count: 4 }),
  dept({ id: 'd2', name: 'Backend Guild', parent_dept_id: 'd1', agent_count: 2 }),
  dept({ id: 'd3', name: 'Marketing', agent_count: 0 }),
];

function mockFetch(depts: unknown[] = DEPTS, opts: { pending?: boolean } = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/departments')) {
      if (opts.pending) return new Promise(() => {}) as Promise<Response>;
      return new Response(JSON.stringify(depts), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderTree(onDeptSelect?: (d: OrgDepartment) => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><DepartmentTree orgId="org-1" onDeptSelect={onDeptSelect} /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('DepartmentTree', () => {
  test('renders root departments and their agent counts', async () => {
    mockFetch();
    renderTree();
    expect(await screen.findByText('Engineering')).toBeInTheDocument();
    expect(screen.getByText('Marketing')).toBeInTheDocument();
    // agent_count 4 for Engineering renders as a tabular number.
    expect(screen.getByText('4')).toBeInTheDocument();
  });

  test('renders a nested child under an expanded root department', async () => {
    mockFetch();
    renderTree();
    // Root nodes default to open (depth === 0), so the child is visible.
    expect(await screen.findByText('Backend Guild')).toBeInTheDocument();
  });

  test('shows the loading skeleton while departments load', () => {
    mockFetch(DEPTS, { pending: true });
    const { container } = renderTree();
    expect(container.querySelector('.animate-pulse')).not.toBeNull();
    expect(screen.queryByText('Engineering')).not.toBeInTheDocument();
  });

  test('shows the empty state when there are no departments', async () => {
    mockFetch([]);
    renderTree();
    expect(await screen.findByText(/No departments yet/i)).toBeInTheDocument();
  });

  test('clicking a department invokes onDeptSelect with that department', async () => {
    mockFetch();
    const onSelect = vi.fn();
    renderTree(onSelect);
    await screen.findByText('Marketing');
    await userEvent.click(screen.getByText('Marketing'));
    await waitFor(() => expect(onSelect).toHaveBeenCalled());
    expect(onSelect.mock.calls[0][0]).toMatchObject({ id: 'd3', name: 'Marketing' });
  });

  test('collapsing a root department hides its child', async () => {
    mockFetch();
    renderTree();
    expect(await screen.findByText('Backend Guild')).toBeInTheDocument();
    // The Engineering button toggles open→closed.
    await userEvent.click(screen.getByRole('button', { name: /Engineering/ }));
    await waitFor(() => expect(screen.queryByText('Backend Guild')).not.toBeInTheDocument());
  });
});
