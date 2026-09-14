/**
 * Tests for AgentConstellation — Task 9 live agent-network beams.
 * Mirrors org.test.tsx's wrap() + framer-motion mock pattern.
 *
 * The component's real message/kind data comes in via props
 * (`communicatingPairs` + `recentMessages`), so no hook mocking is needed for
 * that part. `useConstellationLayout` (d3-force, async import + rAF ticking)
 * is mocked to synchronously hand back fixed node positions — the least
 * invasive way to make beam rendering deterministic without touching the
 * component's real prop contract.
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import React, { type ReactNode, useEffect } from 'react';

import { AgentConstellation } from '../components/AgentConstellation';
import type { OrgRecentMessage } from '../hooks/useOrgNeuralState';

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

// ─── Mock useConstellationLayout — the real hook resolves d3-force via a
// dynamic import and drives positions off requestAnimationFrame, which isn't
// deterministic in jsdom. Beams only render once both endpoints have a
// position, so we hand back fixed coordinates for every node synchronously. ──
vi.mock('@/hooks/useConstellationLayout', () => ({
  useConstellationLayout: (
    nodes: Array<{ id: string }>,
    _edges: unknown[],
    opts: { onTick?: (positions: Map<string, { x: number; y: number }>) => void },
  ) => {
    useEffect(() => {
      const positions = new Map<string, { x: number; y: number }>();
      nodes.forEach((n, i) => positions.set(n.id, { x: 100 + i * 60, y: 100 + i * 40 }));
      opts.onTick?.(positions);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [nodes.length]);
    return { stopSim: () => {} };
  },
}));

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

const AGENTS = [
  { id: 'agent-1', label: 'Agent One', status: 'active' as const, goalCount: 1 },
  { id: 'agent-2', label: 'Agent Two', status: 'idle' as const, goalCount: 0 },
];

const MESSAGE: OrgRecentMessage = {
  id: 'msg-1', from: 'agent-1', to: 'agent-2', kind: 'proposal', at: Date.now(),
};

const BEAM_TOOLTIP = 'agent-1 → agent-2 · proposal';

describe('AgentConstellation', () => {
  it('renders a beam for a communicating pair with a recent message, colored/labeled by kind', () => {
    wrap(
      <AgentConstellation
        orgId=""
        missions={[]}
        agents={AGENTS}
        communicatingPairs={[['agent-1', 'agent-2']]}
        recentMessages={[MESSAGE]}
        onBeamSelect={vi.fn()}
      />
    );

    const beam = screen.getByRole('button', { name: BEAM_TOOLTIP, hidden: true });
    expect(beam).toBeInTheDocument();
    expect(beam.querySelector('title')?.textContent).toBe(BEAM_TOOLTIP);
  });

  it('calls onBeamSelect with the message id when the beam is clicked', () => {
    const onBeamSelect = vi.fn();
    wrap(
      <AgentConstellation
        orgId=""
        missions={[]}
        agents={AGENTS}
        communicatingPairs={[['agent-1', 'agent-2']]}
        recentMessages={[MESSAGE]}
        onBeamSelect={onBeamSelect}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: BEAM_TOOLTIP, hidden: true }));
    expect(onBeamSelect).toHaveBeenCalledTimes(1);
    expect(onBeamSelect).toHaveBeenCalledWith('msg-1');
  });

  it('dedupes a [s,t] pair present in both team and message pairs to a single beam', () => {
    wrap(
      <AgentConstellation
        orgId=""
        missions={[]}
        agents={AGENTS}
        // Same unordered pair twice — once as a team.formed pair (no fixed
        // direction), once as the message pair — must render exactly once.
        communicatingPairs={[['agent-2', 'agent-1'], ['agent-1', 'agent-2']]}
        recentMessages={[MESSAGE]}
        onBeamSelect={vi.fn()}
      />
    );

    const beams = screen.getAllByRole('button', { name: BEAM_TOOLTIP, hidden: true });
    expect(beams).toHaveLength(1);
  });
});
