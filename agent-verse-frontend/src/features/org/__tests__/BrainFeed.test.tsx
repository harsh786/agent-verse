/**
 * Tests for BrainFeed — the org-brain decisions timeline.
 *
 * Mirrors the provider/mock setup in `org.test.tsx`: QueryClientProvider +
 * MemoryRouter wrapper, framer-motion stubbed to avoid animation timing.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import React, { type ReactNode } from 'react';

import type { BrainDecision } from '../types';

// ─── Mock framer-motion to avoid animation timing issues in tests ─────────────
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
      get: (target, key: string) => key in target ? target[key] : makeStub(key),
    }),
  };
});

// ─── Mock the org autonomy API ────────────────────────────────────────────────
const decisionsMock = vi.fn();
vi.mock('@/features/org/api', () => ({
  orgAutonomyApi: {
    decisions: (...args: unknown[]) => decisionsMock(...args),
  },
}));

// ─── Test wrapper ──────────────────────────────────────────────────────────────

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

// ─── Fixtures ──────────────────────────────────────────────────────────────────

function buildDecision(overrides?: Partial<BrainDecision>): BrainDecision {
  return {
    id:                'd-001',
    tick_id:           'tick-001',
    kind:              'execute',
    rationale:         null,
    target_goal:       null,
    action:            null,
    guardrail_verdict: null,
    reason:            null,
    est_cost_usd:      null,
    mission_id:        null,
    created_at:        '2026-09-14T10:00:00Z',
    ...overrides,
  };
}

const EXECUTED_DECISION = buildDecision({
  id:                'd-executed',
  kind:              'execute',
  action:            'launched',
  guardrail_verdict: 'allow',
  reason:            null,
  rationale:         'ship it',
  est_cost_usd:      1.25,
  mission_id:        'm-001',
  created_at:        '2026-09-14T10:05:00Z',
});

const BLOCKED_DECISION = buildDecision({
  id:                'd-blocked',
  kind:              'propose',
  action:            null,
  guardrail_verdict: 'blocked',
  reason:            'over budget',
  rationale:         'wanted to launch a new mission',
  est_cost_usd:      50,
  created_at:        '2026-09-14T09:00:00Z',
});

describe('BrainFeed', () => {
  it('renders both executed and blocked decisions, with the blocked reason and held-back treatment', async () => {
    decisionsMock.mockResolvedValueOnce([EXECUTED_DECISION, BLOCKED_DECISION]);

    const { BrainFeed } = await import('../components/BrainFeed');
    wrap(<BrainFeed orgId="org-001" />);

    await waitFor(() => expect(decisionsMock).toHaveBeenCalledWith('org-001', 50));

    // Executed row: shows its action + rationale
    expect(await screen.findByText('launched')).toBeInTheDocument();
    expect(screen.getByText('ship it')).toBeInTheDocument();

    // Blocked row: shows its block reason + a distinguishing "held back" label
    expect(screen.getByText('over budget')).toBeInTheDocument();
    expect(screen.getByText(/held back/i)).toBeInTheDocument();
  });

  it('shows an empty state when there are no decisions', async () => {
    decisionsMock.mockResolvedValueOnce([]);

    const { BrainFeed } = await import('../components/BrainFeed');
    wrap(<BrainFeed orgId="org-002" />);

    expect(await screen.findByText(/no autonomous decisions yet/i)).toBeInTheDocument();
  });

  it('shows an error state when the fetch fails', async () => {
    decisionsMock.mockRejectedValueOnce(new Error('network down'));

    const { BrainFeed } = await import('../components/BrainFeed');
    wrap(<BrainFeed orgId="org-003" />);

    expect(await screen.findByText(/failed to load/i)).toBeInTheDocument();
  });
});
