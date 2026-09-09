import { renderHook, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { useTaskHandoffAnimations } from './useTaskHandoffAnimations';
import { orgKeys } from './useOrg';

const { apiFetchMock } = vi.hoisted(() => ({ apiFetchMock: vi.fn() }));
vi.mock('@/lib/api/client', () => ({ apiFetch: apiFetchMock }));

describe('useTaskHandoffAnimations', () => {
  beforeEach(() => {
    apiFetchMock.mockReset();
    vi.useRealTimers();
  });

  function wrapper(qc: QueryClient) {
    return function Wrapper({ children }: { children: ReactNode }) {
      return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
    };
  }

  /**
   * Pre-seed the cache with the FIRST snapshot (synchronously, before mount)
   * and disable refetch-on-mount, so the hook's very first render already
   * reflects it. That leaves exactly one async transition per test —
   * snapshot1 -> snapshot2 via a real invalidate — instead of two
   * back-to-back resolutions racing each other, which is what actually
   * happens on a live SSE-driven org (one event, one refetch at a time).
   */
  function seedQueryClient(initial: unknown) {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnMount: false } },
    });
    qc.setQueryData(orgKeys.tasks('org-1', {}), initial);
    return qc;
  }

  it('starts empty, and surfaces a handoff once a real task completes and a teammate has in-flight work', async () => {
    const qc = seedQueryClient({
      data: [
        { id: 't1', mission_id: 'm-1', assigned_to: 'agent-a', status: 'running', title: 'Research' },
        { id: 't2', mission_id: 'm-1', assigned_to: 'agent-b', status: 'queued', title: 'Write draft' },
      ],
    });

    const { result } = renderHook(() => useTaskHandoffAnimations('org-1', 5000), { wrapper: wrapper(qc) });
    expect(result.current).toEqual([]);

    // A real event (agent completes task) causes a refetch that now shows t1 completed.
    apiFetchMock.mockResolvedValueOnce({
      data: [
        { id: 't1', mission_id: 'm-1', assigned_to: 'agent-a', status: 'completed', title: 'Research' },
        { id: 't2', mission_id: 'm-1', assigned_to: 'agent-b', status: 'queued', title: 'Write draft' },
      ],
    });
    await act(async () => {
      await qc.invalidateQueries({ queryKey: orgKeys.tasks('org-1', {}) });
    });

    await waitFor(() => expect(result.current).toHaveLength(1));
    expect(result.current[0]).toMatchObject({
      fromAgentId: 'agent-a', toAgentId: 'agent-b', toTaskId: 't2', label: 'Write draft',
    });
  });

  it('clears the handoff after its animation window elapses', async () => {
    const qc = seedQueryClient({
      data: [
        { id: 't1', mission_id: 'm-1', assigned_to: 'agent-a', status: 'running', title: 'Research' },
        { id: 't2', mission_id: 'm-1', assigned_to: 'agent-b', status: 'queued', title: 'Write draft' },
      ],
    });
    const { result } = renderHook(() => useTaskHandoffAnimations('org-1', 150), { wrapper: wrapper(qc) });

    apiFetchMock.mockResolvedValueOnce({
      data: [
        { id: 't1', mission_id: 'm-1', assigned_to: 'agent-a', status: 'completed', title: 'Research' },
        { id: 't2', mission_id: 'm-1', assigned_to: 'agent-b', status: 'queued', title: 'Write draft' },
      ],
    });
    await act(async () => {
      await qc.invalidateQueries({ queryKey: orgKeys.tasks('org-1', {}) });
    });
    await waitFor(() => expect(result.current).toHaveLength(1));
    await waitFor(() => expect(result.current).toHaveLength(0), { timeout: 3000 });
  });

  it('never fabricates a handoff when nothing actually changed', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const snapshot = {
      data: [
        { id: 't1', mission_id: 'm-1', assigned_to: 'agent-a', status: 'completed', title: 'Research' },
        { id: 't2', mission_id: 'm-1', assigned_to: 'agent-b', status: 'queued', title: 'Write draft' },
      ],
    };
    apiFetchMock.mockResolvedValue(snapshot);

    const { result } = renderHook(() => useTaskHandoffAnimations('org-1', 50), { wrapper: wrapper(qc) });
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledTimes(1));

    await act(async () => {
      await qc.invalidateQueries({ queryKey: orgKeys.tasks('org-1', {}) });
    });
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledTimes(2));
    expect(result.current).toEqual([]);
  });
});
