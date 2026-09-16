import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import React, { type ReactNode } from 'react';
import { useAuthStore } from '@/stores/auth';
import { DecisionLog } from './DecisionLog';

// framer-motion's height/opacity springs are irrelevant to these assertions and
// its AnimatePresence exit timing can hide freshly-expanded content in jsdom, so
// stub it to render children synchronously.
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

const DECISIONS = [
  {
    id: 'd1',
    goal_id: 'g1',
    step: 'Deploy service to production',
    reasoning: 'Change is high risk and requires an explicit human review',
    outcome: 'approved',
    confidence: 0.92,
    risk_level: 'high',
    created_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 'd2',
    goal_id: 'g1',
    step: 'Read the config file',
    reasoning: 'Safe read-only operation, auto-approved',
    outcome: 'rejected',
    confidence: 0.4,
    risk_level: 'low',
    created_at: '2026-01-02T00:00:00Z',
    overridden_by: 'admin@example.com',
  },
];

function mockFetch(decisions: unknown[] = DECISIONS) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/v1/governance/decisions'))
      return new Response(JSON.stringify(decisions), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderLog(props: Record<string, unknown> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <DecisionLog {...props} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('DecisionLog', () => {
  test('renders each decision with its step, risk badge and confidence percentage', async () => {
    mockFetch();
    renderLog();
    expect(await screen.findByText('Deploy service to production')).toBeInTheDocument();
    expect(screen.getByText('Read the config file')).toBeInTheDocument();
    // confidence 0.92 → 92%, 0.4 → 40%
    expect(screen.getByText('92%')).toBeInTheDocument();
    expect(screen.getByText('40%')).toBeInTheDocument();
    // risk badges
    expect(screen.getByText('high')).toBeInTheDocument();
    expect(screen.getByText('low')).toBeInTheDocument();
  });

  test('expanding a decision reveals its reasoning and override note', async () => {
    mockFetch();
    renderLog();
    const row = await screen.findByText('Read the config file');
    // reasoning hidden until the row is expanded
    expect(screen.queryByText(/Safe read-only operation/i)).not.toBeInTheDocument();
    await userEvent.click(row);
    expect(screen.getByText(/Safe read-only operation, auto-approved/i)).toBeInTheDocument();
    expect(screen.getByText(/Overridden by: admin@example.com/i)).toBeInTheDocument();
  });

  test('the outcome filter narrows the visible decisions', async () => {
    mockFetch();
    renderLog();
    await screen.findByText('Deploy service to production');
    await userEvent.selectOptions(screen.getByLabelText('Filter by outcome'), 'approved');
    expect(screen.getByText('Deploy service to production')).toBeInTheDocument();
    expect(screen.queryByText('Read the config file')).not.toBeInTheDocument();
  });

  test('scopes the request with the agent_id and goal_id query params', async () => {
    const spy = mockFetch();
    renderLog({ agentId: 'agent-7', goalId: 'goal-42' });
    await screen.findByText('Deploy service to production');
    expect(
      spy.mock.calls.some(([u]) => {
        const s = String(u);
        return s.includes('agent_id=agent-7') && s.includes('goal_id=goal-42');
      }),
    ).toBe(true);
  });

  test('renders the empty state when no decisions are returned', async () => {
    mockFetch([]);
    renderLog();
    expect(await screen.findByText('No decisions logged')).toBeInTheDocument();
    expect(screen.getByText(/Decisions appear as the agent executes goals/i)).toBeInTheDocument();
  });
});
