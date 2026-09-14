/**
 * Tests for MissionGantt — the mission phase-timing ribbon.
 *
 * Mirrors the provider/mock setup in `org.test.tsx` / `BrainFeed.test.tsx`:
 * QueryClientProvider wrapper, framer-motion stubbed to avoid animation
 * timing issues.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import type { MissionTimeline } from '../types';

// ─── Mock framer-motion to avoid animation timing issues in tests ─────────────
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  return {
    ...actual,
    useReducedMotion: () => true,
  };
});

// ─── Mock the situation API ────────────────────────────────────────────────────
const timelineMock = vi.fn();
vi.mock('@/features/org/api', () => ({
  situationApi: {
    missionTimeline: (...args: unknown[]) => timelineMock(...args),
  },
}));

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const TIMELINE: MissionTimeline = {
  mission_id: 'm-001',
  status:     'completed',
  total_ms:   3_723_000, // 1h 2m 3s
  phases: [
    { name: 'planning',  at: '2026-09-14T09:00:00Z', until: '2026-09-14T09:10:00Z', duration_ms: 600_000, agent: 'planner-1' },
    { name: 'executing', at: '2026-09-14T09:10:00Z', until: '2026-09-14T10:01:03Z', duration_ms: 3_063_000, agent: 'executor-2' },
    { name: 'done',      at: '2026-09-14T10:01:03Z', until: '2026-09-14T10:02:03Z', duration_ms: 60_000, agent: null },
  ],
};

describe('MissionGantt', () => {
  beforeEach(() => {
    timelineMock.mockReset();
  });

  it('renders phases with labels, proportional widths, and the header total', async () => {
    timelineMock.mockResolvedValueOnce(TIMELINE);

    const { MissionGantt } = await import('../components/MissionGantt');
    wrap(<MissionGantt orgId="org-001" missionId="m-001" />);

    await waitFor(() => expect(timelineMock).toHaveBeenCalledWith('org-001', 'm-001'));

    // Header total, formatted h/m/s
    expect(await screen.findByText('1h 2m 3s')).toBeInTheDocument();

    // Phase names appear (segment label + legend)
    expect(screen.getAllByText('planning').length).toBeGreaterThan(0);
    expect(screen.getAllByText('executing').length).toBeGreaterThan(0);
    expect(screen.getAllByText('done').length).toBeGreaterThan(0);

    // Legend duration text
    expect(screen.getByText('10m 0s')).toBeInTheDocument();
    expect(screen.getByText('51m 3s')).toBeInTheDocument();
    expect(screen.getByText('1m 0s')).toBeInTheDocument();

    // Agent shown for phases that have one, omitted for the one that doesn't
    expect(screen.getByText('· planner-1')).toBeInTheDocument();
    expect(screen.getByText('· executor-2')).toBeInTheDocument();

    // Proportional widths: executing (3,063,000ms) is the widest segment of
    // the 3,723,000ms total — roughly 82.3%.
    const ribbon = screen.getByRole('img', { name: /mission phases/i });
    const segments = Array.from(ribbon.children) as HTMLElement[];
    expect(segments).toHaveLength(3);
    const widths = segments.map(s => parseFloat(s.style.width));
    expect(widths[1]).toBeGreaterThan(widths[0]);
    expect(widths[1]).toBeGreaterThan(widths[2]);
    expect(widths[1]).toBeCloseTo(82.28, 1);
  });

  it('shows an empty state when the mission has no phases', async () => {
    timelineMock.mockResolvedValueOnce({ mission_id: 'm-002', status: 'draft', total_ms: null, phases: [] });

    const { MissionGantt } = await import('../components/MissionGantt');
    wrap(<MissionGantt orgId="org-001" missionId="m-002" />);

    expect(await screen.findByText(/no timeline yet/i)).toBeInTheDocument();
  });

  it('shows an error state when the fetch fails', async () => {
    timelineMock.mockRejectedValueOnce(new Error('network down'));

    const { MissionGantt } = await import('../components/MissionGantt');
    wrap(<MissionGantt orgId="org-001" missionId="m-003" />);

    expect(await screen.findByText(/failed to load timeline/i)).toBeInTheDocument();
  });
});
