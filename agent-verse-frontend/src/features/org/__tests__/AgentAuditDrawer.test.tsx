/**
 * Tests for AgentAuditDrawer — the per-agent audit trail drawer.
 *
 * Mirrors the provider/mock setup in `BrainFeed.test.tsx` / `org.test.tsx`:
 * QueryClientProvider + MemoryRouter wrapper, framer-motion stubbed to avoid
 * animation timing, situationApi mocked at the module boundary.
 */
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import React, { type ReactNode } from 'react';

import type { AgentAuditEntry } from '../types';

// ─── Mock framer-motion to avoid animation timing issues in tests ─────────────
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  // Cache one stub component per tag: a Proxy `get` trap fires on every
  // property access (e.g. `motion.div` is re-evaluated on every render of the
  // component under test), so an uncached factory would hand back a brand-new
  // function identity each render — React would then see a different `type`
  // for the same JSX position and remount the whole stubbed subtree on every
  // re-render, silently destroying DOM/focus state between renders.
  const stubCache = new Map<string, (props: { children?: ReactNode; [k: string]: unknown }) => React.ReactElement>();
  const makeStub = (tag: string) => {
    let stub = stubCache.get(tag);
    if (!stub) {
      stub = ({ children, ...props }) => React.createElement(tag, props as Record<string, unknown>, children);
      stubCache.set(tag, stub);
    }
    return stub;
  };
  return {
    ...actual,
    useReducedMotion: () => true,
    AnimatePresence: ({ children }: { children?: ReactNode }) => <>{children}</>,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => key in target ? target[key] : makeStub(key),
    }),
  };
});

// ─── Mock the situation-room API ───────────────────────────────────────────────
const agentAuditMock = vi.fn();
vi.mock('@/features/org/api', () => ({
  situationApi: {
    agentAudit: (...args: unknown[]) => agentAuditMock(...args),
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
function buildEntry(overrides?: Partial<AgentAuditEntry>): AgentAuditEntry {
  return {
    id:          'e-001',
    kind:        'event',
    at:          '2026-09-14T10:00:00Z',
    title:       'Something happened',
    detail:      'detail text',
    cost_usd:    null,
    duration_ms: null,
    mission_id:  null,
    ref:         {},
    ...overrides,
  };
}

const MESSAGE_ENTRY = buildEntry({
  id: 'e-msg', kind: 'message', title: 'Sent status update',
  detail: 'Told the planner it finished step 2', at: '2026-09-14T10:05:00Z',
});

const DECISION_ENTRY = buildEntry({
  id: 'e-dec', kind: 'decision', title: 'Chose search over browse',
  detail: 'Picked the search tool because it was cheaper', at: '2026-09-14T10:10:00Z',
  ref: { table: 'brain_decisions', id: 'd-42' },
});

const TASK_ENTRY = buildEntry({
  id: 'e-task', kind: 'task', title: 'Completed subtask',
  detail: 'Wrote the summary report', at: '2026-09-14T10:15:00Z',
  cost_usd: 0.42, duration_ms: 1500,
});

describe('AgentAuditDrawer', () => {
  beforeEach(() => {
    agentAuditMock.mockReset();
  });

  it('renders mixed entries with kind badges, timestamps, cost and duration', async () => {
    agentAuditMock.mockResolvedValueOnce([DECISION_ENTRY, TASK_ENTRY, MESSAGE_ENTRY]);

    const { AgentAuditDrawer } = await import('../components/AgentAuditDrawer');
    wrap(<AgentAuditDrawer orgId="org-1" agentId="agent-1" onClose={vi.fn()} />);

    await waitFor(() => expect(agentAuditMock).toHaveBeenCalledWith('org-1', 'agent-1'));

    expect(await screen.findByText('Chose search over browse')).toBeInTheDocument();
    expect(screen.getByText('Completed subtask')).toBeInTheDocument();
    expect(screen.getByText('Sent status update')).toBeInTheDocument();

    // Kind badges
    expect(screen.getByText('Decision')).toBeInTheDocument();
    expect(screen.getByText('Task')).toBeInTheDocument();
    expect(screen.getByText('Message')).toBeInTheDocument();

    // Cost + duration on the task entry
    expect(screen.getByText('$0.42')).toBeInTheDocument();
    expect(screen.getByText('1.5s')).toBeInTheDocument();
  });

  it('shows an "explain" control on decision entries that reveals detail + ref on activate', async () => {
    agentAuditMock.mockResolvedValueOnce([DECISION_ENTRY]);

    const { AgentAuditDrawer } = await import('../components/AgentAuditDrawer');
    wrap(<AgentAuditDrawer orgId="org-1" agentId="agent-1" onClose={vi.fn()} />);

    // Wait for the settled (post-fetch) render before querying — the drawer's
    // motion-stubbed subtree remounts on the loading→loaded transition, so an
    // element reference grabbed too early can go stale.
    await screen.findByText('Chose search over browse');
    expect(screen.getByRole('button', { name: /explain decision/i })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText(/brain_decisions/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /explain decision/i }));

    // Re-query rather than reuse the pre-click node reference (same remount caveat).
    expect(screen.getByRole('button', { name: /explain decision/i })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText(/brain_decisions/)).toBeInTheDocument();
    expect(screen.getByText(/d-42/)).toBeInTheDocument();
  });

  it('does not show the explain control on non-decision entries', async () => {
    agentAuditMock.mockResolvedValueOnce([TASK_ENTRY]);

    const { AgentAuditDrawer } = await import('../components/AgentAuditDrawer');
    wrap(<AgentAuditDrawer orgId="org-1" agentId="agent-1" onClose={vi.fn()} />);

    await screen.findByText('Completed subtask');
    expect(screen.queryByRole('button', { name: /explain/i })).not.toBeInTheDocument();
  });

  it('shows an empty state when the agent has no recorded activity', async () => {
    agentAuditMock.mockResolvedValueOnce([]);

    const { AgentAuditDrawer } = await import('../components/AgentAuditDrawer');
    wrap(<AgentAuditDrawer orgId="org-1" agentId="agent-2" onClose={vi.fn()} />);

    expect(await screen.findByText(/no recorded activity for this agent/i)).toBeInTheDocument();
  });

  it('shows an error state when the fetch fails', async () => {
    agentAuditMock.mockRejectedValueOnce(new Error('network down'));

    const { AgentAuditDrawer } = await import('../components/AgentAuditDrawer');
    wrap(<AgentAuditDrawer orgId="org-1" agentId="agent-3" onClose={vi.fn()} />);

    expect(await screen.findByText(/failed to load audit trail/i)).toBeInTheDocument();
  });

  it('calls onClose when the close button is clicked', async () => {
    agentAuditMock.mockResolvedValueOnce([]);
    const onClose = vi.fn();

    const { AgentAuditDrawer } = await import('../components/AgentAuditDrawer');
    wrap(<AgentAuditDrawer orgId="org-1" agentId="agent-4" onClose={onClose} />);

    // Wait for the settled (post-fetch) render first — see comment above.
    await screen.findByText(/no recorded activity/i);
    fireEvent.click(screen.getByRole('button', { name: /^close$/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it('calls onClose on Escape', async () => {
    agentAuditMock.mockResolvedValueOnce([]);
    const onClose = vi.fn();

    const { AgentAuditDrawer } = await import('../components/AgentAuditDrawer');
    wrap(<AgentAuditDrawer orgId="org-1" agentId="agent-5" onClose={onClose} />);

    await screen.findByText(/no recorded activity/i);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('does not steal focus back to the panel on an unrelated parent re-render', async () => {
    agentAuditMock.mockResolvedValueOnce([DECISION_ENTRY]);
    const onClose = vi.fn();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const { AgentAuditDrawer } = await import('../components/AgentAuditDrawer');
    const { rerender } = render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <AgentAuditDrawer orgId="org-1" agentId="agent-1" onClose={onClose} />
        </MemoryRouter>
      </QueryClientProvider>
    );

    await screen.findByText('Chose search over browse');

    // Move focus to an interactive element inside the drawer, away from the
    // panel container that the mount-time focus-trap-lite effect focuses.
    const explainButton = screen.getByRole('button', { name: /explain decision/i });
    explainButton.focus();
    expect(explainButton).toHaveFocus();

    // Simulate a parent re-render that passes a brand-new `onClose` identity
    // (mirrors OrgPage's old inline `() => setSelectedAgentId(null)` before
    // the fix, and any other unrelated re-render in between, e.g. SSE state
    // updates). The mount-focus effect is keyed on `agentId`, not `onClose`,
    // so it must NOT re-run and must NOT pull focus back to the panel.
    const newOnClose = vi.fn();
    act(() => {
      rerender(
        <QueryClientProvider client={qc}>
          <MemoryRouter>
            <AgentAuditDrawer orgId="org-1" agentId="agent-1" onClose={newOnClose} />
          </MemoryRouter>
        </QueryClientProvider>
      );
    });

    expect(explainButton.isConnected).toBe(true);
    expect(explainButton).toHaveFocus();
  });
});
