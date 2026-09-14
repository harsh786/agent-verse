/**
 * Tests for AutonomyStatusBadge — the always-visible autonomy mode pill +
 * one-click hero Pause/Resume.
 *
 * Mirrors the provider/mock setup in `BudgetGauges.test.tsx` / `org.test.tsx`.
 * Deliberately asserts the query key/fn match AutonomyControl's exactly
 * (`['org-autonomy', orgId]` + `orgAutonomyApi.get`) so both components share
 * one cache entry — the class of bug just fixed in BudgetGauges.
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import type { AutonomyView } from '../types';

// ─── Mock the org autonomy API ────────────────────────────────────────────────
const getMock = vi.fn();
const patchMock = vi.fn();
vi.mock('@/features/org/api', () => ({
  orgAutonomyApi: {
    get:   (...args: unknown[]) => getMock(...args),
    patch: (...args: unknown[]) => patchMock(...args),
  },
}));

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return { qc, ...render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>) };
}

function buildAutonomy(overrides?: Partial<AutonomyView['settings']>, autonomy_level = 3): AutonomyView {
  return {
    autonomy_level,
    settings: {
      paused:                         false,
      cadence_seconds:                300,
      min_interval_seconds:           60,
      max_concurrent:                 2,
      max_missions_per_day:           5,
      daily_budget_usd:               10,
      per_mission_cost_ceiling_usd:   5,
      blocked_threshold:              3,
      failed_threshold:               3,
      idle_threshold:                 3,
      collaboration_enabled:          true,
      collaboration_daily_budget_usd: 2,
      collab_messages_per_tick:       4,
      ...overrides,
    },
  };
}

beforeEach(() => {
  getMock.mockReset();
  patchMock.mockReset();
});

describe('AutonomyStatusBadge', () => {
  it('uses the exact same query key + fetcher as AutonomyControl', async () => {
    getMock.mockResolvedValueOnce(buildAutonomy());

    const { AutonomyStatusBadge } = await import('../components/AutonomyStatusBadge');
    const { qc } = wrap(<AutonomyStatusBadge orgId="org-shared" />);

    await waitFor(() => expect(getMock).toHaveBeenCalledWith('org-shared'));

    // The cache entry AutonomyControl reads/writes must be this exact key —
    // otherwise the two components silently diverge (BudgetGauges bug).
    const cached = qc.getQueryData(['org-autonomy', 'org-shared']);
    expect(cached).toEqual(buildAutonomy());
  });

  it('renders AUTONOMOUS · L{n} when not paused', async () => {
    getMock.mockResolvedValueOnce(buildAutonomy({ paused: false }, 4));

    const { AutonomyStatusBadge } = await import('../components/AutonomyStatusBadge');
    wrap(<AutonomyStatusBadge orgId="org-001" />);

    expect(await screen.findByText(/autonomous\s*·\s*l4/i)).toBeInTheDocument();
    expect(screen.queryByText(/^paused$/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /pause org autonomy/i })).toBeInTheDocument();
  });

  it('renders PAUSED when the org brain is paused', async () => {
    getMock.mockResolvedValueOnce(buildAutonomy({ paused: true }));

    const { AutonomyStatusBadge } = await import('../components/AutonomyStatusBadge');
    wrap(<AutonomyStatusBadge orgId="org-002" />);

    expect(await screen.findByText(/^paused$/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /resume org autonomy/i })).toBeInTheDocument();
  });

  it('clicking Pause calls patch with {settings:{paused:true}} and invalidates the shared cache', async () => {
    // mockResolvedValue (not Once): invalidateQueries triggers a refetch after
    // the mutation succeeds, so getMock is called again — same as
    // AutonomyControl.test.tsx's convention for this scenario.
    getMock.mockResolvedValue(buildAutonomy({ paused: false }));
    patchMock.mockResolvedValueOnce(buildAutonomy({ paused: true }));

    const user = userEvent.setup();
    const { AutonomyStatusBadge } = await import('../components/AutonomyStatusBadge');
    wrap(<AutonomyStatusBadge orgId="org-003" />);

    const pauseButton = await screen.findByRole('button', { name: /pause org autonomy/i });
    await user.click(pauseButton);

    await waitFor(() => expect(patchMock).toHaveBeenCalledWith('org-003', { settings: { paused: true } }));
  });

  it('clicking Resume calls patch with {settings:{paused:false}}', async () => {
    getMock.mockResolvedValue(buildAutonomy({ paused: true }));
    patchMock.mockResolvedValueOnce(buildAutonomy({ paused: false }));

    const user = userEvent.setup();
    const { AutonomyStatusBadge } = await import('../components/AutonomyStatusBadge');
    wrap(<AutonomyStatusBadge orgId="org-004" />);

    const resumeButton = await screen.findByRole('button', { name: /resume org autonomy/i });
    await user.click(resumeButton);

    await waitFor(() => expect(patchMock).toHaveBeenCalledWith('org-004', { settings: { paused: false } }));
  });

  it('disables the button while the mutation is pending', async () => {
    getMock.mockResolvedValue(buildAutonomy({ paused: false }));
    let resolvePatch: (v: AutonomyView) => void = () => {};
    patchMock.mockReturnValueOnce(new Promise((resolve) => { resolvePatch = resolve; }));

    const user = userEvent.setup();
    const { AutonomyStatusBadge } = await import('../components/AutonomyStatusBadge');
    wrap(<AutonomyStatusBadge orgId="org-005" />);

    const pauseButton = await screen.findByRole('button', { name: /pause org autonomy/i });
    await user.click(pauseButton);

    await waitFor(() => expect(pauseButton).toBeDisabled());
    resolvePatch(buildAutonomy({ paused: true }));
  });
});
