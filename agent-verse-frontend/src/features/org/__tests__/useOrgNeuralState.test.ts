/**
 * useOrgNeuralState — reducer tests (Task 9: live agent-network beams driven
 * by real collaboration messages).
 *
 * No DOM assertions needed here; this exercises the reducer/applyEvent
 * contract directly via renderHook. Time-dependent behavior (transient beam
 * decay) is driven entirely by a mocked clock (vi.useFakeTimers +
 * vi.setSystemTime / vi.advanceTimersByTime) — never real timers — so the
 * expiry assertion is deterministic.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { useOrgNeuralState, MESSAGE_BEAM_DECAY_MS } from '../hooks/useOrgNeuralState';
import { ORG_EVENTS, type OrgEvent } from '../OrgRealtimeManager';

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return React.createElement(QueryClientProvider, { client: qc }, children);
}

function buildEvent(overrides: Partial<OrgEvent> = {}): OrgEvent {
  return {
    event_type: ORG_EVENTS.COLLABORATION_MESSAGE,
    org_id:     'org-1',
    tenant_id:  't-1',
    payload:    { from_agent: 'agent-a', to: 'team', kind: 'proposal', message: 'status update' },
    timestamp:  '2026-09-14T00:00:00.000Z',
    version:    '1',
    ...overrides,
  };
}

describe('useOrgNeuralState — collaboration messages', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-14T00:00:00.000Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('adds a communicating pair (from → __hub__) and a recentMessages entry for a broadcast message', () => {
    const { result } = renderHook(() => useOrgNeuralState(null), { wrapper });

    act(() => {
      result.current.applyEvent(buildEvent());
    });

    expect(result.current.communicatingPairs).toContainEqual(['agent-a', '__hub__']);
    expect(result.current.recentMessages).toHaveLength(1);
    expect(result.current.recentMessages[0]).toMatchObject({
      from: 'agent-a', to: '__hub__', kind: 'proposal',
    });
  });

  it('routes a direct (non-"team") message straight to the target agent, no virtual hub', () => {
    const { result } = renderHook(() => useOrgNeuralState(null), { wrapper });

    act(() => {
      result.current.applyEvent(buildEvent({
        payload: { from_agent: 'agent-a', to: 'agent-b', kind: 'question' },
      }));
    });

    expect(result.current.communicatingPairs).toContainEqual(['agent-a', 'agent-b']);
    expect(result.current.recentMessages[0]).toMatchObject({
      from: 'agent-a', to: 'agent-b', kind: 'question',
    });
  });

  it('prepends a second message (newest-first) without dropping the first, up to the cap', () => {
    const { result } = renderHook(() => useOrgNeuralState(null), { wrapper });

    act(() => {
      result.current.applyEvent(buildEvent({
        correlation_id: 'c1',
        payload: { from_agent: 'agent-a', to: 'team', kind: 'proposal' },
      }));
    });
    act(() => {
      vi.advanceTimersByTime(1_000);
      result.current.applyEvent(buildEvent({
        correlation_id: 'c2',
        payload: { from_agent: 'agent-b', to: 'team', kind: 'result' },
      }));
    });

    expect(result.current.recentMessages).toHaveLength(2);
    expect(result.current.recentMessages[0]).toMatchObject({ from: 'agent-b', kind: 'result' });
    expect(result.current.recentMessages[1]).toMatchObject({ from: 'agent-a', kind: 'proposal' });
    // Distinct ids derived from correlation_id.
    expect(result.current.recentMessages[0].id).not.toEqual(result.current.recentMessages[1].id);
  });

  it('lets a message-driven communicating pair expire after its decay window', () => {
    const { result, rerender } = renderHook(() => useOrgNeuralState(null), { wrapper });

    act(() => {
      result.current.applyEvent(buildEvent());
    });
    expect(result.current.communicatingPairs).toContainEqual(['agent-a', '__hub__']);

    act(() => {
      vi.advanceTimersByTime(MESSAGE_BEAM_DECAY_MS + 1_000);
    });
    // Force the hook to re-run and recompute the expiry filter against the
    // now-advanced clock (no reliance on the background prune timer firing).
    rerender();

    expect(result.current.communicatingPairs).not.toContainEqual(['agent-a', '__hub__']);
    // The message itself stays in the history buffer — only the live beam pair expires.
    expect(result.current.recentMessages).toHaveLength(1);
  });

  it('still handles org.team.formed as before, unaffected by message-pair decay', () => {
    const { result, rerender } = renderHook(() => useOrgNeuralState(null), { wrapper });

    act(() => {
      result.current.applyEvent({
        event_type: ORG_EVENTS.TEAM_FORMED,
        org_id: 'org-1', tenant_id: 't-1',
        payload: { agent_ids: ['agent-x', 'agent-y'] },
        timestamp: '2026-09-14T00:00:00.000Z', version: '1',
      });
    });
    expect(result.current.communicatingPairs).toContainEqual(['agent-x', 'agent-y']);

    // Team-formed pairs are NOT time-based — they must survive well past the
    // message decay window.
    act(() => {
      vi.advanceTimersByTime(MESSAGE_BEAM_DECAY_MS + 5_000);
    });
    rerender();
    expect(result.current.communicatingPairs).toContainEqual(['agent-x', 'agent-y']);

    // They still clear on mission completion, exactly as before this change.
    act(() => {
      result.current.applyEvent({
        event_type: ORG_EVENTS.MISSION_COMPLETED,
        org_id: 'org-1', tenant_id: 't-1', payload: {},
        timestamp: '2026-09-14T00:00:10.000Z', version: '1',
      });
    });
    expect(result.current.communicatingPairs).not.toContainEqual(['agent-x', 'agent-y']);
  });

  it('still maps org.agent.activated to an executing agent node (unrelated event handling untouched)', () => {
    const { result } = renderHook(() => useOrgNeuralState(null), { wrapper });

    act(() => {
      result.current.applyEvent({
        event_type: ORG_EVENTS.AGENT_ACTIVATED,
        org_id: 'org-1', tenant_id: 't-1',
        payload: { agent_id: 'agent-a', role: 'Researcher' },
        timestamp: '2026-09-14T00:00:00.000Z', version: '1',
      });
    });

    expect(result.current.agents).toContainEqual(
      expect.objectContaining({ id: 'agent-a', state: 'executing', role: 'Researcher' }),
    );
  });
});
