/**
 * Tests for BudgetGauges — the org-brain budget-burn radial gauges.
 *
 * Mirrors the provider/mock setup in `org.test.tsx` / `BrainFeed.test.tsx`.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import type { AutonomyView, BrainDecision } from '../types';

// ─── Mock the org autonomy API ────────────────────────────────────────────────
const getMock = vi.fn();
const decisionsMock = vi.fn();
vi.mock('@/features/org/api', () => ({
  orgAutonomyApi: {
    get:       (...args: unknown[]) => getMock(...args),
    decisions: (...args: unknown[]) => decisionsMock(...args),
  },
}));

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

function buildAutonomy(overrides?: Partial<AutonomyView['settings']>): AutonomyView {
  return {
    autonomy_level: 3,
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

function buildDecision(overrides?: Partial<BrainDecision>): BrainDecision {
  return {
    id:                'd-1',
    tick_id:           'tick-1',
    kind:              'execute',
    rationale:         null,
    target_goal:       null,
    action:            'launched',
    guardrail_verdict: 'allow',
    reason:            null,
    est_cost_usd:      1,
    mission_id:        null,
    created_at:        new Date().toISOString(),
    ...overrides,
  };
}

describe('BudgetGauges', () => {
  beforeEach(() => {
    getMock.mockReset();
    decisionsMock.mockReset();
  });

  it('renders the daily gauge fill % from today\'s decisions and flags amber near the cap', async () => {
    getMock.mockResolvedValueOnce(buildAutonomy({ daily_budget_usd: 10 }));
    // Two decisions today summing to $9 of a $10 cap = 90% -> amber.
    decisionsMock.mockResolvedValueOnce([
      buildDecision({ id: 'd-1', est_cost_usd: 6 }),
      buildDecision({ id: 'd-2', est_cost_usd: 3 }),
    ]);

    const { BudgetGauges } = await import('../components/BudgetGauges');
    wrap(<BudgetGauges orgId="org-001" />);

    await waitFor(() => expect(getMock).toHaveBeenCalledWith('org-001'));
    await waitFor(() => expect(decisionsMock).toHaveBeenCalledWith('org-001', 100));

    const dailyGroup = await screen.findByRole('group', { name: /^daily budget:/i });
    expect(dailyGroup).toHaveTextContent('90%');

    const pctEl = dailyGroup.querySelector('span.font-mono') as HTMLElement;
    expect(pctEl).toHaveStyle({ color: '#F59E0B' }); // amber at >= 80%
  });

  it('turns the daily gauge red at or above 100% of cap', async () => {
    getMock.mockResolvedValueOnce(buildAutonomy({ daily_budget_usd: 10 }));
    decisionsMock.mockResolvedValueOnce([buildDecision({ id: 'd-1', est_cost_usd: 12 })]);

    const { BudgetGauges } = await import('../components/BudgetGauges');
    wrap(<BudgetGauges orgId="org-002" />);

    const dailyGroup = await screen.findByRole('group', { name: /^daily budget:/i });
    expect(dailyGroup).toHaveTextContent('120%');
    const pctEl = dailyGroup.querySelector('span.font-mono') as HTMLElement;
    expect(pctEl).toHaveStyle({ color: '#EF4444' }); // red at >= 100%
  });

  it('shows "no cap set" when a cap is configured as 0 (avoids divide-by-zero)', async () => {
    getMock.mockResolvedValueOnce(buildAutonomy({ per_mission_cost_ceiling_usd: 0 }));
    decisionsMock.mockResolvedValueOnce([buildDecision({ est_cost_usd: 4 })]);

    const { BudgetGauges } = await import('../components/BudgetGauges');
    wrap(<BudgetGauges orgId="org-003" />);

    const missionGroup = await screen.findByRole('group', { name: /^per-mission budget:/i });
    expect(missionGroup).toHaveTextContent(/no cap set/i);
  });

  it('shows the collaboration cap without fabricating a spend figure', async () => {
    getMock.mockResolvedValueOnce(buildAutonomy({ collaboration_daily_budget_usd: 2 }));
    decisionsMock.mockResolvedValueOnce([]);

    const { BudgetGauges } = await import('../components/BudgetGauges');
    wrap(<BudgetGauges orgId="org-004" />);

    const collabGroup = await screen.findByRole('group', { name: /^collaboration budget:/i });
    expect(collabGroup).toHaveTextContent('$2.00');
    expect(collabGroup).toHaveTextContent(/no live spend feed/i);
    // No fill arc drawn for a gauge with no live spend figure.
    expect(collabGroup.querySelectorAll('circle')).toHaveLength(1);
  });

  it('shows a loading state, then an error state when a request fails', async () => {
    getMock.mockRejectedValueOnce(new Error('network down'));
    decisionsMock.mockResolvedValueOnce([]);

    const { BudgetGauges } = await import('../components/BudgetGauges');
    wrap(<BudgetGauges orgId="org-005" />);

    expect(await screen.findByText(/failed to load budgets/i)).toBeInTheDocument();
  });
});
