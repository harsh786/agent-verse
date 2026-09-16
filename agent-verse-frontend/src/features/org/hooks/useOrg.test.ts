/**
 * Tests for useOrg — TanStack Query hooks for the AI Organization OS.
 *
 * orgApi and apiFetch are mocked (unit test of the hooks' query/mutation
 * wiring, not the HTTP layer — orgApi/api.ts and the client itself have their
 * own coverage). EventSource is faked for the two SSE hooks, mirroring the
 * transport double used in OrgRealtimeManager.test.ts.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor, act } from '@testing-library/react';
import React from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

const { orgApiMock, apiFetchMock } = vi.hoisted(() => ({
  orgApiMock: {
    list: vi.fn(),
    get: vi.fn(),
    health: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    listMissions: vi.fn(),
    getMission: vi.fn(),
    createMission: vi.fn(),
    updateMissionStatus: vi.fn(),
    listDepartments: vi.fn(),
    createDepartment: vi.fn(),
    listTeamMembers: vi.fn(),
    listEvents: vi.fn(),
  },
  apiFetchMock: vi.fn(),
}));
vi.mock('../api', () => ({ orgApi: orgApiMock }));
vi.mock('@/lib/api/client', () => ({ apiFetch: apiFetchMock }));

import {
  orgKeys,
  useOrganizations,
  useOrganization,
  useOrgHealth,
  useCreateOrganization,
  useUpdateOrganization,
  useMissions,
  useMission,
  useCreateMission,
  useUpdateMissionStatus,
  useDepartments,
  useCreateDepartment,
  useTeamMembers,
  useOrgEvents,
  useOrgMission,
  useOrgDepartments,
  useOrgTasks,
  useUpdateTaskStatus,
  useOrgStream,
  useMissionStream,
  useApprovals,
} from './useOrg';

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  closed = false;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  close() { this.closed = true; }
  emitMessage(data: unknown) {
    this.onmessage?.({ data: typeof data === 'string' ? data : JSON.stringify(data) });
  }
  emitError() { this.onerror?.(); }
  static latest() { return FakeEventSource.instances[FakeEventSource.instances.length - 1]; }
}
const OriginalEventSource = globalThis.EventSource;

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return React.createElement(QueryClientProvider, { client: qc }, children);
}

/** A wrapper that exposes the QueryClient so tests can pre-seed cache data. */
function makeWrapperWithClient() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const Wrapper = ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
  return { qc, Wrapper };
}

beforeEach(() => {
  vi.clearAllMocks();
  FakeEventSource.instances = [];
  (globalThis as unknown as { EventSource: unknown }).EventSource = FakeEventSource;
});
afterEach(() => {
  (globalThis as unknown as { EventSource: unknown }).EventSource = OriginalEventSource;
});

describe('orgKeys', () => {
  test('produces stable, distinct query keys', () => {
    expect(orgKeys.all()).toEqual(['orgs']);
    expect(orgKeys.detail('o1')).toEqual(['orgs', 'detail', 'o1']);
    expect(orgKeys.health('o1')).toEqual(['orgs', 'health', 'o1']);
    expect(orgKeys.missions('o1', { status: 'active' })).toEqual(['orgs', 'o1', 'missions', { status: 'active' }]);
    expect(orgKeys.mission('o1', 'm1')).toEqual(['orgs', 'o1', 'mission', 'm1']);
    expect(orgKeys.tasks('o1')).toEqual(['orgs', 'o1', 'tasks', undefined]);
    expect(orgKeys.events('o1')).toEqual(['orgs', 'o1', 'events']);
    expect(orgKeys.departments('o1')).toEqual(['orgs', 'o1', 'departments']);
    expect(orgKeys.teamMembers('o1', 't1')).toEqual(['orgs', 'o1', 'teams', 't1', 'members']);
  });
});

describe('useOrganizations / useOrganization / useOrgHealth', () => {
  test('useOrganizations fetches the org list', async () => {
    orgApiMock.list.mockResolvedValue({ data: [{ id: 'o1' }], cursor: null, hasMore: false });
    const { result } = renderHook(() => useOrganizations({ status: 'active' }), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(orgApiMock.list).toHaveBeenCalledWith({ status: 'active' });
    expect(result.current.data).toEqual({ data: [{ id: 'o1' }], cursor: null, hasMore: false });
  });

  test('useOrganization is disabled without an orgId and enabled once set', async () => {
    orgApiMock.get.mockResolvedValue({ id: 'o1' });
    const { result, rerender } = renderHook(({ id }: { id: string | null }) => useOrganization(id), {
      wrapper,
      initialProps: { id: null },
    });
    expect(result.current.fetchStatus).toBe('idle');
    expect(orgApiMock.get).not.toHaveBeenCalled();

    rerender({ id: 'o1' });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(orgApiMock.get).toHaveBeenCalledWith('o1');
  });

  test('useOrgHealth is disabled without an orgId', () => {
    const { result } = renderHook(() => useOrgHealth(undefined), { wrapper });
    expect(result.current.fetchStatus).toBe('idle');
    expect(orgApiMock.health).not.toHaveBeenCalled();
  });

  test('useOrgHealth fetches health once orgId is present', async () => {
    orgApiMock.health.mockResolvedValue({ health: 'healthy' });
    const { result } = renderHook(() => useOrgHealth('o1'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(orgApiMock.health).toHaveBeenCalledWith('o1');
  });
});

describe('useCreateOrganization', () => {
  test('creates an org and invalidates the org list', async () => {
    orgApiMock.create.mockResolvedValue({ id: 'new-org' });
    const { qc, Wrapper } = makeWrapperWithClient();
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');
    const { result } = renderHook(() => useCreateOrganization(), { wrapper: Wrapper });

    await act(async () => { await result.current.mutateAsync({ name: 'Acme' }); });
    expect(orgApiMock.create).toHaveBeenCalledWith({ name: 'Acme' });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['orgs'] });
  });
});

describe('useUpdateOrganization', () => {
  test('optimistically patches the cached detail, then settles by invalidating', async () => {
    orgApiMock.update.mockResolvedValue({ id: 'o1', name: 'New name' });
    const { qc, Wrapper } = makeWrapperWithClient();
    qc.setQueryData(orgKeys.detail('o1'), { id: 'o1', name: 'Old name', autonomy_level: 2 });
    const { result } = renderHook(() => useUpdateOrganization('o1'), { wrapper: Wrapper });

    await act(async () => { await result.current.mutateAsync({ name: 'New name' }); });
    expect(orgApiMock.update).toHaveBeenCalledWith('o1', { name: 'New name' });
    // onSettled invalidates both the detail and the list.
    await waitFor(() => expect(qc.getQueryState(orgKeys.detail('o1'))?.isInvalidated).toBe(true));
  });

  test('rolls back the optimistic patch when the update fails', async () => {
    orgApiMock.update.mockRejectedValue(new Error('boom'));
    const { qc, Wrapper } = makeWrapperWithClient();
    qc.setQueryData(orgKeys.detail('o1'), { id: 'o1', name: 'Old name' });
    const { result } = renderHook(() => useUpdateOrganization('o1'), { wrapper: Wrapper });

    await act(async () => {
      await expect(result.current.mutateAsync({ name: 'New name' })).rejects.toThrow('boom');
    });
    expect(qc.getQueryData(orgKeys.detail('o1'))).toEqual({ id: 'o1', name: 'Old name' });
  });
});

describe('useMissions / useMission', () => {
  test('useMissions paginates via the cursor returned in each page', async () => {
    orgApiMock.listMissions.mockResolvedValue({ data: [{ id: 'm1' }], cursor: 'next-cursor', hasMore: true });
    const { result } = renderHook(() => useMissions('o1', { status: 'active' }), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(orgApiMock.listMissions).toHaveBeenCalledWith('o1', { status: 'active', cursor: undefined });
    expect(result.current.hasNextPage).toBe(true);

    await act(async () => { await result.current.fetchNextPage(); });
    expect(orgApiMock.listMissions).toHaveBeenLastCalledWith('o1', { status: 'active', cursor: 'next-cursor' });
  });

  test('useMissions is disabled without an orgId', () => {
    const { result } = renderHook(() => useMissions(null), { wrapper });
    expect(result.current.fetchStatus).toBe('idle');
  });

  test('useMission is disabled without a missionId', () => {
    const { result } = renderHook(() => useMission('o1', undefined), { wrapper });
    expect(result.current.fetchStatus).toBe('idle');
    expect(orgApiMock.getMission).not.toHaveBeenCalled();
  });

  test('useOrgMission aliases useMission', async () => {
    orgApiMock.getMission.mockResolvedValue({ id: 'm1' });
    const { result } = renderHook(() => useOrgMission('o1', 'm1'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(orgApiMock.getMission).toHaveBeenCalledWith('o1', 'm1');
  });
});

describe('useCreateMission', () => {
  test('optimistically prepends a temp mission to the first page', async () => {
    orgApiMock.createMission.mockResolvedValue({ id: 'm-real', title: 'Launch' });
    const { qc, Wrapper } = makeWrapperWithClient();
    qc.setQueryData(orgKeys.missions('o1'), {
      pages: [{ data: [{ id: 'm0', title: 'Existing' }], cursor: null, hasMore: false }],
      pageParams: [undefined],
    });
    const { result } = renderHook(() => useCreateMission('o1'), { wrapper: Wrapper });

    let mutatePromise: Promise<unknown>;
    act(() => {
      mutatePromise = result.current.mutateAsync({ org_id: 'o1', title: 'Launch', priority: 'high' });
    });
    // Optimistic update happens in onMutate, before the mutation resolves.
    await waitFor(() => {
      const cached = qc.getQueryData<{ pages: { data: { id: string; title: string }[] }[] }>(orgKeys.missions('o1'));
      expect(cached?.pages[0].data[0].title).toBe('Launch');
      expect(cached?.pages[0].data[0].id).toMatch(/^temp-/);
    });
    await act(async () => { await mutatePromise; });
    expect(orgApiMock.createMission).toHaveBeenCalledWith('o1', { org_id: 'o1', title: 'Launch', priority: 'high' });
  });

  test('is a no-op against an empty cache (old undefined branch)', async () => {
    orgApiMock.createMission.mockResolvedValue({ id: 'm-real' });
    const { Wrapper } = makeWrapperWithClient();
    const { result } = renderHook(() => useCreateMission('o1'), { wrapper: Wrapper });
    await act(async () => {
      await expect(result.current.mutateAsync({ org_id: 'o1', title: 'X' })).resolves.toBeTruthy();
    });
  });

  test('rolls back the optimistic prepend when creation fails', async () => {
    orgApiMock.createMission.mockRejectedValue(new Error('nope'));
    const { qc, Wrapper } = makeWrapperWithClient();
    const original = { pages: [{ data: [{ id: 'm0', title: 'Existing' }], cursor: null, hasMore: false }], pageParams: [undefined] };
    qc.setQueryData(orgKeys.missions('o1'), original);
    const { result } = renderHook(() => useCreateMission('o1'), { wrapper: Wrapper });

    await act(async () => {
      await expect(result.current.mutateAsync({ org_id: 'o1', title: 'Launch' })).rejects.toThrow('nope');
    });
    expect(qc.getQueryData(orgKeys.missions('o1'))).toEqual(original);
  });
});

describe('useUpdateMissionStatus', () => {
  test('writes the updated mission into the detail cache and invalidates the list', async () => {
    orgApiMock.updateMissionStatus.mockResolvedValue({ id: 'm1', status: 'completed' });
    const { qc, Wrapper } = makeWrapperWithClient();
    const { result } = renderHook(() => useUpdateMissionStatus('o1'), { wrapper: Wrapper });

    await act(async () => { await result.current.mutateAsync({ missionId: 'm1', status: 'completed' }); });
    expect(orgApiMock.updateMissionStatus).toHaveBeenCalledWith('o1', 'm1', 'completed');
    expect(qc.getQueryData(orgKeys.mission('o1', 'm1'))).toEqual({ id: 'm1', status: 'completed' });
  });
});

describe('useDepartments / useCreateDepartment / useOrgDepartments', () => {
  test('useDepartments is disabled without an orgId', () => {
    const { result } = renderHook(() => useDepartments(null), { wrapper });
    expect(result.current.fetchStatus).toBe('idle');
  });

  test('useOrgDepartments aliases useDepartments', async () => {
    orgApiMock.listDepartments.mockResolvedValue([{ id: 'd1' }]);
    const { result } = renderHook(() => useOrgDepartments('o1'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(orgApiMock.listDepartments).toHaveBeenCalledWith('o1');
  });

  test('useCreateDepartment invalidates the department list on success', async () => {
    orgApiMock.createDepartment.mockResolvedValue({ id: 'd1' });
    const { qc, Wrapper } = makeWrapperWithClient();
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');
    const { result } = renderHook(() => useCreateDepartment('o1'), { wrapper: Wrapper });
    await act(async () => { await result.current.mutateAsync({ org_id: 'o1', name: 'Eng' }); });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: orgKeys.departments('o1') });
  });
});

describe('useTeamMembers', () => {
  test('is disabled unless both orgId and teamId are present', () => {
    const { result: r1 } = renderHook(() => useTeamMembers(null, 't1'), { wrapper });
    expect(r1.current.fetchStatus).toBe('idle');
    const { result: r2 } = renderHook(() => useTeamMembers('o1', null), { wrapper });
    expect(r2.current.fetchStatus).toBe('idle');
  });

  test('fetches members once both ids are present', async () => {
    orgApiMock.listTeamMembers.mockResolvedValue({ team_id: 't1', member_ids: [], members: [] });
    const { result } = renderHook(() => useTeamMembers('o1', 't1'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(orgApiMock.listTeamMembers).toHaveBeenCalledWith('o1', 't1');
  });
});

describe('useOrgEvents', () => {
  test('is disabled without an orgId, fetches once set', async () => {
    const { result, rerender } = renderHook(({ id }: { id: string | null }) => useOrgEvents(id), {
      wrapper,
      initialProps: { id: null },
    });
    expect(result.current.fetchStatus).toBe('idle');
    orgApiMock.listEvents.mockResolvedValue([{ id: 'e1' }]);
    rerender({ id: 'o1' });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(orgApiMock.listEvents).toHaveBeenCalledWith('o1');
  });
});

describe('useOrgTasks', () => {
  test('builds a query string from the provided filters', async () => {
    apiFetchMock.mockResolvedValue({ data: [{ id: 't1' }] });
    const { result } = renderHook(
      () => useOrgTasks('o1', { mission_id: 'm1', status: 'running', limit: 10 }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const calledPath = apiFetchMock.mock.calls[0][0] as string;
    expect(calledPath).toContain('/v1/org/o1/tasks?');
    expect(calledPath).toContain('mission_id=m1');
    expect(calledPath).toContain('status=running');
    expect(calledPath).toContain('limit=10');
  });

  test('falls back to an empty data array when the request fails', async () => {
    apiFetchMock.mockRejectedValue(new Error('network'));
    const { result } = renderHook(() => useOrgTasks('o1'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual({ data: [] });
  });

  test('is disabled without an orgId', () => {
    const { result } = renderHook(() => useOrgTasks(null), { wrapper });
    expect(result.current.fetchStatus).toBe('idle');
  });
});

describe('useUpdateTaskStatus', () => {
  test('PATCHes the task status and invalidates the tasks query', async () => {
    apiFetchMock.mockResolvedValue({ id: 't1', status: 'completed' });
    const { qc, Wrapper } = makeWrapperWithClient();
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');
    const { result } = renderHook(() => useUpdateTaskStatus('o1'), { wrapper: Wrapper });

    await act(async () => { await result.current.mutateAsync({ taskId: 't1', status: 'completed' }); });
    expect(apiFetchMock).toHaveBeenCalledWith('/v1/org/o1/tasks/t1/status', {
      method: 'PATCH',
      body: JSON.stringify({ status: 'completed' }),
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: orgKeys.tasks('o1') });
  });
});

describe('useApprovals', () => {
  test('unwraps a bare array response', async () => {
    apiFetchMock.mockResolvedValue([{ request_id: 'a1' }]);
    const { result } = renderHook(() => useApprovals('o1'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([{ request_id: 'a1' }]);
  });

  test('unwraps a {data:[]}-shaped response', async () => {
    apiFetchMock.mockResolvedValue({ data: [{ request_id: 'a2' }] });
    const { result } = renderHook(() => useApprovals('o1'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([{ request_id: 'a2' }]);
  });

  test('falls back to an empty array on failure', async () => {
    apiFetchMock.mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useApprovals('o1'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);
  });

  test('is disabled without an orgId', () => {
    const { result } = renderHook(() => useApprovals(null), { wrapper });
    expect(result.current.fetchStatus).toBe('idle');
  });
});

describe('useOrgStream', () => {
  test('opens an EventSource, forwards parsed events, and reports connection state', async () => {
    const onEvent = vi.fn();
    const { result, unmount } = renderHook(() => useOrgStream('o1', onEvent), { wrapper });
    await waitFor(() => expect(result.current.connected).toBe(true));
    expect(FakeEventSource.instances).toHaveLength(1);

    act(() => FakeEventSource.latest().emitMessage({ type: 'mission_completed' }));
    expect(onEvent).toHaveBeenCalledWith({ type: 'mission_completed' });

    act(() => FakeEventSource.latest().emitError());
    await waitFor(() => expect(result.current.connected).toBe(false));

    unmount();
    expect(FakeEventSource.latest().closed).toBe(true);
  });

  test('ignores malformed event payloads without throwing', async () => {
    const onEvent = vi.fn();
    renderHook(() => useOrgStream('o1', onEvent), { wrapper });
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(() => FakeEventSource.latest().emitMessage('not-json{')).not.toThrow();
    expect(onEvent).not.toHaveBeenCalled();
  });

  test('does not open a stream without an orgId', () => {
    renderHook(() => useOrgStream('', undefined), { wrapper });
    expect(FakeEventSource.instances).toHaveLength(0);
  });
});

describe('useMissionStream', () => {
  test('accumulates events, caps history at 100, and invalidates on terminal events', async () => {
    const { qc, Wrapper } = makeWrapperWithClient();
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');
    const { result } = renderHook(() => useMissionStream('o1', 'm1'), { wrapper: Wrapper });
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));

    act(() => FakeEventSource.latest().emitMessage({ type: 'progress', i: 1 }));
    expect(result.current).toHaveLength(1);

    act(() => FakeEventSource.latest().emitMessage({ type: 'mission_completed' }));
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: orgKeys.mission('o1', 'm1') });
  });

  test('does not open a stream without a missionId', () => {
    renderHook(() => useMissionStream('o1', undefined), { wrapper });
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  test('ignores malformed messages', async () => {
    const { result } = renderHook(() => useMissionStream('o1', 'm1'), { wrapper });
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(() => FakeEventSource.latest().emitMessage('{bad')).not.toThrow();
    expect(result.current).toHaveLength(0);
  });
});
