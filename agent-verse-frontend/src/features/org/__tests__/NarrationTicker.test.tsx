/**
 * Tests for NarrationTicker — the plain-English running narration of what
 * the org is doing, and its pure `narrate()` event -> line mapping.
 *
 * Mirrors the provider/mock setup in `TeamChannel.test.tsx` / `BrainFeed.test.tsx`:
 * framer-motion stubbed, `../OrgRealtimeManager` mocked so the test can push
 * live events straight through the captured `onEvent` callback.
 */
import { act, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import React, { type ReactNode } from 'react';

import type { OrgEvent } from '../OrgRealtimeManager';

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

// ─── Mock the org realtime manager, capturing the passed onEvent ──────────────
let capturedOnEvent: ((event: OrgEvent) => void) | null = null;
vi.mock('../OrgRealtimeManager', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../OrgRealtimeManager')>();
  return {
    ...actual,
    useOrgRealtimeManager: (_orgId: string, options: { onEvent?: (event: OrgEvent) => void }) => {
      capturedOnEvent = options.onEvent ?? null;
      return { connected: true };
    },
  };
});

beforeEach(() => {
  capturedOnEvent = null;
});

function buildEvent(overrides?: Partial<OrgEvent>): OrgEvent {
  return {
    event_type: 'org.mission.started',
    org_id:     'org-001',
    tenant_id:  'tenant-1',
    payload:    {},
    timestamp:  '2026-09-14T10:00:00Z',
    version:    '1',
    ...overrides,
  } as OrgEvent;
}

describe('narrate (pure helper)', () => {
  it('narrates mission lifecycle events with the mission title', async () => {
    const { narrate } = await import('../components/NarrationTicker');

    expect(narrate(buildEvent({ event_type: 'org.mission.created', payload: { title: 'Q3 market scan' } })))
      .toBe("Proposed mission 'Q3 market scan'");
    expect(narrate(buildEvent({ event_type: 'org.mission.started', payload: { title: 'Q3 market scan' } })))
      .toBe("Launched 'Q3 market scan'");
    expect(narrate(buildEvent({ event_type: 'org.mission.completed', payload: { title: 'Q3 market scan' } })))
      .toBe("Completed 'Q3 market scan'");
  });

  it('narrates a collaboration message from the sending agent, truncated', async () => {
    const { narrate } = await import('../components/NarrationTicker');
    const line = narrate(buildEvent({
      event_type: 'org.collaboration.message',
      payload: { from_agent: 'ComplianceAgent', message: 'Vendor lacks SOC2 attestation.' },
    }));
    expect(line).toBe('ComplianceAgent: Vendor lacks SOC2 attestation.');
  });

  it('narrates a held-back brain decision but ignores a normally-executed one', async () => {
    const { narrate } = await import('../components/NarrationTicker');

    const held = narrate(buildEvent({
      event_type: 'org.decision.recorded',
      payload: { guardrail_verdict: 'blocked', reason: 'Daily budget cap reached' },
    }));
    expect(held).toBe('Held back: Daily budget cap reached');

    const executed = narrate(buildEvent({
      event_type: 'org.decision.recorded',
      payload: { guardrail_verdict: 'allow', action: 'launched' },
    }));
    expect(executed).toBeNull();
  });

  it('narrates approval requests and the emergency stop', async () => {
    const { narrate } = await import('../components/NarrationTicker');
    expect(narrate(buildEvent({ event_type: 'org.approval.requested', payload: { title: 'Deploy to prod' } })))
      .toBe('Awaiting approval: Deploy to prod');
    expect(narrate(buildEvent({ event_type: 'org.emergency_stop.triggered', payload: {} })))
      .toBe('EMERGENCY STOP');
  });

  it('returns null for unknown or keyless noise events', async () => {
    const { narrate } = await import('../components/NarrationTicker');
    expect(narrate(buildEvent({ event_type: 'org.digest.ready', payload: {} }))).toBeNull();
    expect(narrate(buildEvent({ event_type: 'org.memory.promoted', payload: {} }))).toBeNull();
    // Collaboration message with no message field — nothing sensible to show.
    expect(narrate(buildEvent({ event_type: 'org.collaboration.message', payload: { from_agent: 'X' } })))
      .toBeNull();
  });
});

describe('NarrationTicker (component)', () => {
  it('shows the empty state before any events arrive', async () => {
    const { NarrationTicker } = await import('../components/NarrationTicker');
    render(<NarrationTicker orgId="org-001" />);

    expect(await screen.findByText(/quiet.*no activity yet/i)).toBeInTheDocument();
    expect(capturedOnEvent).not.toBeNull();
  });

  it('renders narrated lines newest-first as events are pushed through onEvent', async () => {
    const { NarrationTicker } = await import('../components/NarrationTicker');
    render(<NarrationTicker orgId="org-002" />);

    await waitFor(() => expect(capturedOnEvent).not.toBeNull());

    await waitFor(() => {
      capturedOnEvent?.(buildEvent({
        event_type: 'org.mission.started',
        timestamp:  '2026-09-14T10:00:00Z',
        payload:    { title: 'Q3 market scan' },
      }));
    });
    await waitFor(() => {
      capturedOnEvent?.(buildEvent({
        event_type: 'org.collaboration.message',
        timestamp:  '2026-09-14T10:01:00Z',
        payload:    { from_agent: 'ComplianceAgent', message: 'Flagging vendor risk before signoff.' },
      }));
    });
    await waitFor(() => {
      capturedOnEvent?.(buildEvent({
        event_type: 'org.decision.recorded',
        timestamp:  '2026-09-14T10:02:00Z',
        payload:    { guardrail_verdict: 'blocked', reason: 'Daily budget cap reached' },
      }));
    });

    const list = await screen.findByRole('list', { name: /recent narration/i });
    const items = list.querySelectorAll('li');
    expect(items).toHaveLength(3);

    // Newest-first: the held-back decision (last pushed) renders on top.
    expect(items[0]).toHaveTextContent('Held back: Daily budget cap reached');
    expect(items[1]).toHaveTextContent('ComplianceAgent: Flagging vendor risk before signoff.');
    expect(items[2]).toHaveTextContent("Launched 'Q3 market scan'");
  });

  it('ignores noise events end-to-end (no line rendered, empty state stays)', async () => {
    const { NarrationTicker } = await import('../components/NarrationTicker');
    render(<NarrationTicker orgId="org-003" />);

    await waitFor(() => expect(capturedOnEvent).not.toBeNull());
    capturedOnEvent?.(buildEvent({ event_type: 'org.digest.ready', payload: {} }));

    expect(await screen.findByText(/quiet.*no activity yet/i)).toBeInTheDocument();
  });

  it('caps the rendered list at maxItems, dropping the oldest', async () => {
    const { NarrationTicker } = await import('../components/NarrationTicker');
    render(<NarrationTicker orgId="org-004" maxItems={2} />);

    await waitFor(() => expect(capturedOnEvent).not.toBeNull());

    act(() => { capturedOnEvent?.(buildEvent({ event_type: 'org.mission.started', timestamp: 't1', payload: { title: 'First' } })); });
    act(() => { capturedOnEvent?.(buildEvent({ event_type: 'org.mission.started', timestamp: 't2', payload: { title: 'Second' } })); });
    act(() => { capturedOnEvent?.(buildEvent({ event_type: 'org.mission.started', timestamp: 't3', payload: { title: 'Third' } })); });

    const list = await screen.findByRole('list', { name: /recent narration/i });
    const items = list.querySelectorAll('li');
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Launched 'Third'");
    expect(items[1]).toHaveTextContent("Launched 'Second'");
    expect(screen.queryByText(/'First'/)).not.toBeInTheDocument();
  });
});
