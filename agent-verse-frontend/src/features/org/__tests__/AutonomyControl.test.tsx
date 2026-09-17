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

  it('preserves an in-progress Daily-budget edit across a refetch triggered by Pause', async () => {
    const user = userEvent.setup();

    // First GET (initial load) returns the original fixture; the second GET
    // (triggered by invalidateQueries after the Pause mutation) reflects
    // paused: true but the ORIGINAL daily_budget_usd — simulating a refetch
    // that has nothing to do with the user's unsaved Caps edit.
    getMock
      .mockResolvedValueOnce(fixture)
      .mockResolvedValueOnce({
        ...fixture,
        settings: { ...fixture.settings, paused: true },
      });
    patchMock.mockResolvedValue({
      ...fixture,
      settings: { ...fixture.settings, paused: true },
    });

    wrap(<AutonomyControl orgId="org-1" />);

    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(1));

    const budgetInput = await screen.findByLabelText(/^Daily budget/i);
    await user.clear(budgetInput);
    await user.type(budgetInput, '42');
    expect(budgetInput).toHaveValue(42);

    const pauseBtn = await screen.findByRole('button', { name: /pause/i });
    await user.click(pauseBtn);

    // Wait for the mutation + the refetch it triggers to fully settle.
    await waitFor(() => expect(patchMock).toHaveBeenCalledWith('org-1', { settings: { paused: true } }));
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(2));
    await screen.findByRole('button', { name: /resume/i });

    // The unsaved Daily-budget edit must survive the refetch.
    expect(budgetInput).toHaveValue(42);
  });

  it('does not send an emptied/non-numeric budget field as 0/null when saving Caps', async () => {
    const user = userEvent.setup();
    wrap(<AutonomyControl orgId="org-1" />);

    await waitFor(() => expect(getMock).toHaveBeenCalledWith('org-1'));

    const budgetInput = await screen.findByLabelText(/^Daily budget/i);
    await user.clear(budgetInput);

    const saveCapsBtn = await screen.findByRole('button', { name: /save caps/i });
    await user.click(saveCapsBtn);

    await waitFor(() => expect(patchMock).toHaveBeenCalled());
    const [, body] = patchMock.mock.calls[0] as [string, { settings?: Record<string, unknown> }];
    expect(body.settings).not.toHaveProperty('daily_budget_usd');
    // The other, untouched cap fields are still saved as valid numbers.
    expect(body.settings?.max_concurrent).toBe(2);
  });

  it('clicking a non-active level calls patch with the new autonomy_level', async () => {
    const user = userEvent.setup();
    wrap(<AutonomyControl orgId="org-1" />);

    await waitFor(() => expect(getMock).toHaveBeenCalledWith('org-1'));
    await screen.findByText(/L3/);

    const level5Btn = screen.getByRole('radio', { name: 'L5' });
    await user.click(level5Btn);

    await waitFor(() => {
      expect(patchMock).toHaveBeenCalledWith('org-1', { autonomy_level: 5 });
    });
  });

  it('clicking the already-active level is a no-op (does not call patch)', async () => {
    const user = userEvent.setup();
    wrap(<AutonomyControl orgId="org-1" />);

    await waitFor(() => expect(getMock).toHaveBeenCalledWith('org-1'));
    await screen.findByText(/L3/);

    const level3Btn = screen.getByRole('radio', { name: 'L3' });
    await user.click(level3Btn);

    // Give any (incorrect) async patch call a chance to fire.
    await new Promise((r) => setTimeout(r, 0));
    expect(patchMock).not.toHaveBeenCalled();
  });

  it('toggling collaboration calls patch with the flipped collaboration_enabled flag', async () => {
    const user = userEvent.setup();
    wrap(<AutonomyControl orgId="org-1" />);

    await waitFor(() => expect(getMock).toHaveBeenCalledWith('org-1'));

    const collabSwitch = await screen.findByRole('switch', { name: /enable cross-agent collaboration/i });
    await user.click(collabSwitch);

    await waitFor(() => {
      expect(patchMock).toHaveBeenCalledWith('org-1', { settings: { collaboration_enabled: true } });
    });
  });

  it('editing and saving the collaboration daily budget calls patch and resyncs the buffer', async () => {
    const user = userEvent.setup();
    patchMock.mockResolvedValue({
      ...fixture,
      settings: { ...fixture.settings, collaboration_daily_budget_usd: 9 },
    });
    wrap(<AutonomyControl orgId="org-1" />);

    await waitFor(() => expect(getMock).toHaveBeenCalledWith('org-1'));

    const collabBudgetInput = await screen.findByLabelText(/^Collaboration daily budget/i);
    await user.clear(collabBudgetInput);
    await user.type(collabBudgetInput, '9');

    const saveCollabBtn = await screen.findByRole('button', { name: /save collaboration budget/i });
    await user.click(saveCollabBtn);

    await waitFor(() => {
      expect(patchMock).toHaveBeenCalledWith('org-1', { settings: { collaboration_daily_budget_usd: 9 } });
    });
    await waitFor(() => expect(collabBudgetInput).toHaveValue(9));
  });

  it('does not send an emptied/non-numeric collaboration budget field when saving', async () => {
    const user = userEvent.setup();
    wrap(<AutonomyControl orgId="org-1" />);

    await waitFor(() => expect(getMock).toHaveBeenCalledWith('org-1'));

    const collabBudgetInput = await screen.findByLabelText(/^Collaboration daily budget/i);
    await user.clear(collabBudgetInput);

    // The Save button still appears because the (empty) value differs from
    // the saved settings, but clicking it must be a no-op guard.
    const saveCollabBtn = await screen.findByRole('button', { name: /save collaboration budget/i });
    await user.click(saveCollabBtn);

    await new Promise((r) => setTimeout(r, 0));
    expect(patchMock).not.toHaveBeenCalled();
  });

  it('shows a "Failed to save changes" banner when a mutation fails', async () => {
    const user = userEvent.setup();
    patchMock.mockRejectedValue(new Error('save exploded'));
    wrap(<AutonomyControl orgId="org-1" />);

    await waitFor(() => expect(getMock).toHaveBeenCalledWith('org-1'));
    const pauseBtn = await screen.findByRole('button', { name: /pause/i });
    await user.click(pauseBtn);

    await screen.findByText(/failed to save changes: save exploded/i);
  });

  it('shows a generic "Failed to save changes." banner when the mutation error is not an Error instance', async () => {
    const user = userEvent.setup();
    patchMock.mockRejectedValue('nope');
    wrap(<AutonomyControl orgId="org-1" />);

    await waitFor(() => expect(getMock).toHaveBeenCalledWith('org-1'));
    const pauseBtn = await screen.findByRole('button', { name: /pause/i });
    await user.click(pauseBtn);

    await screen.findByText(/failed to save changes\.$/i);
  });
});
