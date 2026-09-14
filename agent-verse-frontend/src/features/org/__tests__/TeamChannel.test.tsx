/**
 * Tests for TeamChannel v2 — the Situation Room team collaboration feed.
 *
 * Mirrors the provider/mock setup in `BrainFeed.test.tsx` / `org.test.tsx`:
 * QueryClientProvider + MemoryRouter wrapper, framer-motion stubbed to avoid
 * animation timing issues. `../OrgRealtimeManager` is mocked so the test can
 * capture the `onEvent` callback passed to `useOrgRealtimeManager` and push a
 * live SSE event through it directly.
 */
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import React, { type ReactNode } from 'react';

import type { CollaborationMessage } from '../types';

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

// ─── Mock the situation API ────────────────────────────────────────────────────
const collaborationHistoryMock = vi.fn();
vi.mock('@/features/org/api', () => ({
  situationApi: {
    collaborationHistory: (...args: unknown[]) => collaborationHistoryMock(...args),
  },
}));

// ─── Mock the org realtime manager, capturing the passed onEvent ──────────────
let capturedOnEvent: ((event: unknown) => void) | null = null;
vi.mock('../OrgRealtimeManager', () => ({
  ORG_EVENTS: {
    COLLABORATION_MESSAGE: 'org.collaboration.message',
  },
  useOrgRealtimeManager: (_orgId: string, options: { onEvent?: (event: unknown) => void }) => {
    capturedOnEvent = options.onEvent ?? null;
    return { connected: true };
  },
}));

// ─── Test wrapper ──────────────────────────────────────────────────────────────

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
  return { ...utils, qc };
}

// ─── Fixtures ──────────────────────────────────────────────────────────────────

function buildMessage(overrides?: Partial<CollaborationMessage>): CollaborationMessage {
  return {
    id:         'msg-001',
    from_agent: 'ResearchAgent',
    to:         'PlannerAgent',
    kind:       'update',
    message:    'Gathered top 5 competitor pricing pages.',
    latency_ms: 420,
    tokens:     150,
    cost_usd:   0.0032,
    mission_id: 'mission-abc-123',
    at:         '2026-09-14T10:00:00Z',
    ...overrides,
  };
}

const RISK_MESSAGE = buildMessage({
  id:         'msg-risk',
  from_agent: 'ComplianceAgent',
  to:         'PlannerAgent',
  kind:       'risk',
  message:    'Proposed vendor lacks SOC2 attestation — flagging before signoff.',
  latency_ms: 890,
  tokens:     310,
  cost_usd:   0.0125,
  mission_id: 'mission-xyz-789',
  at:         '2026-09-14T10:05:00Z',
});

const PROPOSAL_MESSAGE = buildMessage({
  id:         'msg-proposal',
  from_agent: 'PlannerAgent',
  to:         'ExecAgent',
  kind:       'proposal',
  message:    'Suggest splitting the migration into two phases to de-risk rollout.',
  latency_ms: 210,
  tokens:     95,
  cost_usd:   0.0018,
  mission_id: null,
  at:         '2026-09-14T09:55:00Z',
});

beforeEach(() => {
  capturedOnEvent = null;
  collaborationHistoryMock.mockReset();
});

describe('TeamChannel', () => {
  it('renders history rows with their kind chips and latency/tokens/cost meta', async () => {
    collaborationHistoryMock.mockResolvedValueOnce([RISK_MESSAGE, PROPOSAL_MESSAGE]);

    const { TeamChannel } = await import('../components/TeamChannel');
    wrap(<TeamChannel orgId="org-001" />);

    await waitFor(() => expect(collaborationHistoryMock).toHaveBeenCalledWith('org-001', 50));

    // Risk row
    expect(await screen.findByText(/lacks SOC2 attestation/i)).toBeInTheDocument();
    expect(screen.getByText('Risk')).toBeInTheDocument();
    expect(screen.getByText(/890ms/)).toBeInTheDocument();
    expect(screen.getByText(/310 tok/)).toBeInTheDocument();
    expect(screen.getByText(/\$0\.01\b/)).toBeInTheDocument();

    // Proposal row
    expect(screen.getByText(/splitting the migration/i)).toBeInTheDocument();
    expect(screen.getByText('Proposal')).toBeInTheDocument();
    expect(screen.getByText(/210ms/)).toBeInTheDocument();
    expect(screen.getByText(/95 tok/)).toBeInTheDocument();
    expect(screen.getByText(/\$0\.0018/)).toBeInTheDocument();
  });

  it('expands a row to reveal the payload inspector', async () => {
    collaborationHistoryMock.mockResolvedValueOnce([RISK_MESSAGE, PROPOSAL_MESSAGE]);

    const { TeamChannel } = await import('../components/TeamChannel');
    wrap(<TeamChannel orgId="org-002" />);

    const riskButton = await screen.findByRole('button', { name: /ComplianceAgent.*Risk/i });
    expect(riskButton).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(riskButton);

    // Re-query: the mocked motion.li can remount its DOM node across the
    // state update, so assert against the live document rather than the
    // stale pre-click reference.
    expect(screen.getByRole('button', { name: /ComplianceAgent.*Risk/i })).toHaveAttribute('aria-expanded', 'true');
    // Payload inspector: from→to line + mission affordance + raw JSON.
    expect(screen.getByText('ComplianceAgent → PlannerAgent')).toBeInTheDocument();
    expect(screen.getByText(/mission-xy/)).toBeInTheDocument();
    expect(screen.getByText(/"kind": "risk"/)).toBeInTheDocument();
  });

  it('appends a new row when a live COLLABORATION_MESSAGE event is pushed through onEvent', async () => {
    collaborationHistoryMock.mockResolvedValueOnce([PROPOSAL_MESSAGE]);

    const { TeamChannel } = await import('../components/TeamChannel');
    wrap(<TeamChannel orgId="org-003" />);

    await waitFor(() => expect(collaborationHistoryMock).toHaveBeenCalled());
    await screen.findByText(/splitting the migration/i);

    expect(capturedOnEvent).not.toBeNull();

    const liveEvent = {
      event_type:     'org.collaboration.message',
      org_id:         'org-003',
      tenant_id:      'tenant-1',
      payload: {
        from_agent: 'ExecAgent',
        to:         'PlannerAgent',
        kind:       'handoff',
        message:    'Handing final report back for review.',
        latency_ms: 150,
        tokens:     60,
        cost_usd:   0.0009,
        mission_id: 'mission-live-1',
      },
      timestamp:      '2026-09-14T10:10:00Z',
      correlation_id: 'corr-live-1',
      version:        '1',
    };

    await waitFor(() => {
      capturedOnEvent?.(liveEvent);
    });

    expect(await screen.findByText(/Handing final report back for review/i)).toBeInTheDocument();
    expect(screen.getByText('Handoff')).toBeInTheDocument();
  });

  it('dedupes a message delivered live and then seen again in a history refetch (same id)', async () => {
    collaborationHistoryMock.mockResolvedValueOnce([PROPOSAL_MESSAGE]);

    const { TeamChannel } = await import('../components/TeamChannel');
    const { qc } = wrap(<TeamChannel orgId="org-005" />);

    await waitFor(() => expect(collaborationHistoryMock).toHaveBeenCalled());
    await screen.findByText(/splitting the migration/i);

    expect(capturedOnEvent).not.toBeNull();

    // Live event carries the shared id in payload.id (set by
    // CollaborationTick and threaded through to the persisted org_events
    // row's id -- see brain_collaboration.py / service.py).
    const SHARED_ID = 'msg-shared-abc';
    const liveEvent = {
      event_type: 'org.collaboration.message',
      org_id:     'org-005',
      tenant_id:  'tenant-1',
      payload: {
        id:         SHARED_ID,
        from_agent: 'ExecAgent',
        to:         'PlannerAgent',
        kind:       'handoff',
        message:    'Handing final report back for review.',
        latency_ms: 150,
        tokens:     60,
        cost_usd:   0.0009,
        mission_id: 'mission-live-1',
      },
      timestamp:      '2026-09-14T10:10:00Z',
      correlation_id: 'corr-live-2',
      version:        '1',
    };

    await waitFor(() => {
      capturedOnEvent?.(liveEvent);
    });
    expect(await screen.findByText(/Handing final report back for review/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Handing final report back for review/i)).toHaveLength(1);

    // A subsequent history refetch now returns the SAME message, persisted
    // with the SAME id (row.id === payload.id). This must merge into the
    // existing row, not duplicate it.
    collaborationHistoryMock.mockResolvedValueOnce([
      buildMessage({
        id:         SHARED_ID,
        from_agent: 'ExecAgent',
        to:         'PlannerAgent',
        kind:       'handoff',
        message:    'Handing final report back for review.',
        latency_ms: 150,
        tokens:     60,
        cost_usd:   0.0009,
        mission_id: 'mission-live-1',
        at:         '2026-09-14T10:10:00Z',
      }),
      PROPOSAL_MESSAGE,
    ]);

    await act(async () => {
      await qc.refetchQueries({ queryKey: ['org-collaboration', 'org-005'] });
    });

    expect(screen.getAllByText(/Handing final report back for review/i)).toHaveLength(1);
  });

  it('shows an empty state when history is empty', async () => {
    collaborationHistoryMock.mockResolvedValueOnce([]);

    const { TeamChannel } = await import('../components/TeamChannel');
    wrap(<TeamChannel orgId="org-004" />);

    expect(await screen.findByText(/no team chatter yet/i)).toBeInTheDocument();
  });
});
