import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React, { type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ApprovalCenter } from './ApprovalCenter';

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

const APPROVALS = [
  { id: 'ap-1', title: 'Deploy to production', description: 'Ship the v2 release', action_type: 'deploy', risk_level: 'critical', estimated_cost_usd: 1.25, mission_id: null, agent_id: null, prerequisite_approvals: [], already_approved_by: [], approvers_needed: ['lead'], status: 'pending', created_at: '2026-09-16T00:00:00Z', expires_at: null },
  { id: 'ap-2', title: 'Send launch email', description: 'Notify all users', action_type: 'email', risk_level: 'low', estimated_cost_usd: null, mission_id: null, agent_id: null, prerequisite_approvals: [], already_approved_by: [], approvers_needed: [], status: 'pending', created_at: '2026-09-16T00:00:00Z', expires_at: null },
];

function mockFetch(approvals: unknown = APPROVALS) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (/\/approvals\/[^/]+\/(approve|reject)$/.test(url) && method === 'POST')
      return new Response(JSON.stringify({ status: 'ok' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/approvals'))
      return new Response(JSON.stringify(approvals), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderCenter() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ApprovalCenter orgId="o1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ApprovalCenter', () => {
  test('renders pending approvals with the pending count', async () => {
    mockFetch();
    renderCenter();
    expect(screen.getByRole('heading', { name: /Pending Approvals/i })).toBeInTheDocument();
    expect(await screen.findByText('Deploy to production')).toBeInTheDocument();
    expect(screen.getByText('Send launch email')).toBeInTheDocument();
    expect(screen.getByText('Critical risk')).toBeInTheDocument();
    // Count badge reflects the two pending items.
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  test('renders the empty state when there are no pending approvals', async () => {
    mockFetch([]);
    renderCenter();
    expect(await screen.findByText('No pending approvals')).toBeInTheDocument();
  });

  test('approving the top (critical) request POSTs to its approve endpoint', async () => {
    const spy = mockFetch();
    renderCenter();
    await screen.findByText('Deploy to production');
    // critical is sorted first → first Approve button is ap-1.
    fireEvent.click(screen.getAllByRole('button', { name: 'Approve' })[0]);
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => /\/approvals\/ap-1\/approve$/.test(String(u)) && (i as RequestInit)?.method === 'POST'),
      ).toBe(true),
    );
  });

  test('the reject flow reveals a notes field then POSTs to reject', async () => {
    const spy = mockFetch();
    renderCenter();
    await screen.findByText('Deploy to production');
    fireEvent.click(screen.getAllByRole('button', { name: 'Reject' })[0]);
    // Rejection reason textarea appears.
    expect(await screen.findByLabelText(/Rejection reason/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Confirm rejection/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => /\/approvals\/ap-1\/reject$/.test(String(u)) && (i as RequestInit)?.method === 'POST'),
      ).toBe(true),
    );
  });

  test('the refresh button refetches the approvals list', async () => {
    const spy = mockFetch();
    renderCenter();
    await screen.findByText('Deploy to production');
    const before = spy.mock.calls.filter(([u]) => String(u).includes('/approvals')).length;
    fireEvent.click(screen.getByRole('button', { name: /Refresh approvals/i }));
    await waitFor(() =>
      expect(spy.mock.calls.filter(([u]) => String(u).includes('/approvals')).length).toBeGreaterThan(before),
    );
  });
});
