/**
 * Tests for the embeddable AutonomyControl panel
 * (src/features/org/components/AutonomyControl.tsx).
 *
 * NOT to be confused with the top-level full-page L0–L5 selector at
 * src/features/org/AutonomyControl.tsx — that one is untouched.
 *
 * Mirrors the provider/mocking setup in ../__tests__/org.test.tsx.
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import React, { type ReactNode } from 'react';

import { AutonomyControl } from '../components/AutonomyControl';
import type { AutonomyView } from '../types';

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

// ─── Mock the org API module ─────────────────────────────────────────────────
const fixture: AutonomyView = {
  autonomy_level: 3,
  settings: {
    paused: false,
    cadence_seconds: 300,
    min_interval_seconds: 600,
    max_concurrent: 2,
    max_missions_per_day: 8,
    daily_budget_usd: 5,
    per_mission_cost_ceiling_usd: 1,
    blocked_threshold: 5,
    failed_threshold: 2,
    idle_threshold: 1,
    collaboration_enabled: false,
    collaboration_daily_budget_usd: 1,
    collab_messages_per_tick: 4,
  },
};

const getMock = vi.fn();
const patchMock = vi.fn();

vi.mock('@/features/org/api', () => ({
  orgAutonomyApi: {
    get: (...args: unknown[]) => getMock(...args),
    patch: (...args: unknown[]) => patchMock(...args),
    decisions: vi.fn(),
    approveProposal: vi.fn(),
    rejectProposal: vi.fn(),
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

describe('AutonomyControl (embeddable panel)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getMock.mockResolvedValue(fixture);
    patchMock.mockResolvedValue(fixture);
  });

  it('renders the current autonomy level and a Pause toggle', async () => {
    wrap(<AutonomyControl orgId="org-1" />);

    await waitFor(() => expect(getMock).toHaveBeenCalledWith('org-1'));

    // Level 3 is active/current.
    await screen.findByText(/L3/);
    expect(screen.getByRole('button', { name: /pause/i })).toBeInTheDocument();
  });

  it('clicking Pause calls orgAutonomyApi.patch with { settings: { paused: true } }', async () => {
    const user = userEvent.setup();
    wrap(<AutonomyControl orgId="org-1" />);

    // Wait for the async query to settle before interacting.
    await waitFor(() => expect(getMock).toHaveBeenCalledWith('org-1'));
    const pauseBtn = await screen.findByRole('button', { name: /pause/i });

    await user.click(pauseBtn);

    await waitFor(() => {
      expect(patchMock).toHaveBeenCalledWith('org-1', { settings: { paused: true } });
    });
  });

  it('shows a loading state before data resolves', () => {
    getMock.mockReturnValue(new Promise(() => {})); // never resolves
    wrap(<AutonomyControl orgId="org-1" />);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it('shows an error state when the query fails', async () => {
    getMock.mockRejectedValue(new Error('network down'));
    wrap(<AutonomyControl orgId="org-1" />);
    await screen.findByText(/failed|error/i);
  });
});
