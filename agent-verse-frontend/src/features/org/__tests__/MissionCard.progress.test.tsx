/**
 * MissionCard task-progress tests (FE4).
 *
 * OrgMission carries no task/subtask counts of its own (see
 * app/org/schemas.py MissionResponse), so MissionCard derives progress from
 * the real tasks scoped to the mission via `/v1/org/{orgId}/tasks`. These
 * tests verify the card renders the true fraction, hides the bar when there
 * genuinely are no tasks, and shows an honest "—" while the fetch is still
 * in flight — never a fabricated 0%.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import React, { type ReactNode } from 'react';

import { MissionCard } from '../components/MissionCard';
import type { OrgMission } from '../types';

// ─── Mock framer-motion (animation timing is irrelevant to these assertions) ─
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

// ─── Mock the shared API client so we control exactly what the tasks fetch
//     resolves with, without touching orgApi's other endpoints. ──────────────
const { apiFetchMock } = vi.hoisted(() => ({ apiFetchMock: vi.fn() }));
vi.mock('@/lib/api/client', () => ({ apiFetch: apiFetchMock }));

function buildMission(overrides?: Partial<OrgMission>): OrgMission {
  return {
    id:               'm-001',
    tenant_id:        't-001',
    org_id:           'org-001',
    dept_id:          null,
    assigned_team_id: null,
    title:            'Research AI market trends',
    objective:        'Understand competitive landscape',
    why:              'To guide product strategy',
    expected_outcome: 'Comprehensive report',
    status:           'active',
    priority:         'high',
    source:           'manual',
    autonomy_level:   3,
    budget_usd:       null,
    deadline:         null,
    tags:             [],
    created_by:       null,
    outputs:          [],
    evidence:         [],
    started_at:       null,
    completed_at:     null,
    created_at:       '2026-08-17T00:00:00Z',
    updated_at:       '2026-08-17T00:00:00Z',
    ...overrides,
  };
}

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

describe('MissionCard task progress (FE4)', () => {
  beforeEach(() => {
    apiFetchMock.mockReset();
  });

  it('renders the real completed/total fraction once tasks resolve', async () => {
    apiFetchMock.mockResolvedValue({
      data: [
        { id: 't1', status: 'completed' },
        { id: 't2', status: 'completed' },
        { id: 't3', status: 'running' },
      ],
    });

    wrap(<MissionCard mission={buildMission()} orgId="org-001" />);

    await waitFor(() => expect(screen.getByText('2/3')).toBeInTheDocument());
    const bar = screen.getByRole('progressbar');
    expect(bar).toHaveAttribute('aria-valuenow', '67'); // round(2/3 * 100)
    expect(apiFetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/v1/org/org-001/tasks?mission_id=m-001')
    );
  });

  it('hides the progress row when the mission genuinely has no tasks', async () => {
    apiFetchMock.mockResolvedValue({ data: [] });

    wrap(<MissionCard mission={buildMission()} orgId="org-001" />);

    // Wait for the indeterminate state to clear (query resolved) before asserting.
    await waitFor(() => expect(screen.queryByText('—')).not.toBeInTheDocument());
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });

  it('shows an honest "—" placeholder while the fetch is still in flight, never a fake 0%', async () => {
    let resolveFetch!: (v: unknown) => void;
    apiFetchMock.mockReturnValue(new Promise((resolve) => { resolveFetch = resolve; }));

    wrap(<MissionCard mission={buildMission()} orgId="org-001" />);

    // While pending: honest unknown state, not "0/…" or a 0%-filled bar.
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.queryByText(/^0\//)).not.toBeInTheDocument();

    resolveFetch({ data: [{ id: 't1', status: 'completed' }] });
    await waitFor(() => expect(screen.getByText('1/1')).toBeInTheDocument());
  });

  it('does not fetch or render a progress row when orgId is not supplied', () => {
    wrap(<MissionCard mission={buildMission()} />);
    expect(apiFetchMock).not.toHaveBeenCalled();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });
});
