/**
 * Tests for BrainFeed — the org-brain decisions timeline.
 *
 * Mirrors the provider/mock setup in `org.test.tsx`: QueryClientProvider +
 * MemoryRouter wrapper, framer-motion stubbed to avoid animation timing.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import React, { type ReactNode } from 'react';

import type { BrainDecision, GuardrailCheck } from '../types';

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

// ─── Mock the org realtime manager, capturing the passed onEvent ──────────────
let capturedOnEvent: ((event: unknown) => void) | null = null;
vi.mock('../OrgRealtimeManager', () => ({
  ORG_EVENTS: {
    DECISION_RECORDED: 'org.decision.recorded',
  },
  useOrgRealtimeManager: (_orgId: string, options: { onEvent?: (event: unknown) => void }) => {
    capturedOnEvent = options.onEvent ?? null;
    return { connected: true };
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

const TRACE: GuardrailCheck[] = [
  { name: 'kill_switch',    passed: true,  detail: 'kill switch clear',           value: null,  limit: null },
  { name: 'autonomy_level', passed: true,  detail: 'autonomy level sufficient',   value: '4',   limit: '3' },
  { name: 'cooldown',       passed: true,  detail: 'cooldown elapsed',            value: '600s', limit: '300s' },
  { name: 'daily_cap',      passed: false, detail: 'daily autonomous mission cap reached', value: '2', limit: '2' },
];

const BLOCKED_WITH_TRACE = buildDecision({
  id:                'd-blocked-trace',
  kind:              'propose',
  action:            null,
  guardrail_verdict: 'blocked',
  reason:            'daily cap reached',
  rationale:         'wanted to launch another mission',
  est_cost_usd:      3,
  created_at:        '2026-09-14T09:30:00Z',
  guardrail_trace:   TRACE,
});

describe('BrainFeed', () => {
  beforeEach(() => {
    capturedOnEvent = null;
    decisionsMock.mockReset();
  });

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

  it('attributes a held-back decision to the specific failing guardrail (name + number), and expanding reveals the full ordered pass/fail trace', async () => {
    decisionsMock.mockResolvedValueOnce([BLOCKED_WITH_TRACE]);

    const { BrainFeed } = await import('../components/BrainFeed');
    wrap(<BrainFeed orgId="org-004" />);

    await waitFor(() => expect(decisionsMock).toHaveBeenCalledWith('org-004', 50));

    // Held-back summary names the specific guardrail + its number, not just a generic reason.
    expect(await screen.findByText('Daily Cap · 2/2')).toBeInTheDocument();

    const row = screen.getByRole('button', { name: /Daily Cap/i });
    expect(row).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(row);

    expect(screen.getByRole('button', { name: /Daily Cap/i })).toHaveAttribute('aria-expanded', 'true');

    // Full ordered trace: every check rendered, humanized, with pass/fail markers.
    expect(screen.getByText('Kill Switch')).toBeInTheDocument();
    expect(screen.getByText('Autonomy Level')).toBeInTheDocument();
    expect(screen.getByText('Cooldown')).toBeInTheDocument();
    expect(screen.getByText(/daily autonomous mission cap reached/i)).toBeInTheDocument();
    expect(screen.getAllByLabelText('pass')).toHaveLength(3);
    expect(screen.getByLabelText('fail')).toBeInTheDocument();
  });

  it('falls back to the reason text when a blocked decision has no guardrail_trace', async () => {
    decisionsMock.mockResolvedValueOnce([BLOCKED_DECISION]);

    const { BrainFeed } = await import('../components/BrainFeed');
    wrap(<BrainFeed orgId="org-005" />);

    await waitFor(() => expect(decisionsMock).toHaveBeenCalledWith('org-005', 50));

    expect(await screen.findByText('over budget')).toBeInTheDocument();

    const row = screen.getByRole('button', { name: /over budget/i });
    fireEvent.click(row);

    // No trace recorded for this legacy row — expansion falls back to the rationale.
    expect(screen.getByText('wanted to launch a new mission')).toBeInTheDocument();
  });

  it('invalidates the decisions query when a DECISION_RECORDED event arrives via the realtime stream', async () => {
    decisionsMock.mockResolvedValue([EXECUTED_DECISION]);

    const { BrainFeed } = await import('../components/BrainFeed');
    wrap(<BrainFeed orgId="org-006" />);

    await waitFor(() => expect(decisionsMock).toHaveBeenCalledTimes(1));
    expect(capturedOnEvent).not.toBeNull();

    capturedOnEvent?.({
      event_type:     'org.decision.recorded',
      org_id:         'org-006',
      tenant_id:      'tenant-1',
      payload:        {},
      timestamp:      '2026-09-14T10:10:00Z',
      version:        '1',
    });

    await waitFor(() => expect(decisionsMock).toHaveBeenCalledTimes(2));
  });
});
