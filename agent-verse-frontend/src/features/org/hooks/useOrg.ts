/**
 * TanStack Query hooks for the AI Organization OS.
 * All data fetching goes through these hooks — never call orgApi directly in components.
 */
import { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient, useInfiniteQuery } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api/client';
import { orgApi } from '../api';
import type {
  Organization,
  OrgMission,
  CreateOrganizationRequest,
  UpdateOrganizationRequest,
  CreateMissionRequest,
  CreateDepartmentRequest,
  MissionStatus,
} from '../types';

// ── Query key factory ──────────────────────────────────────────────────────

export const orgKeys = {
  all:        ()                 => ['orgs']                              as const,
  list:       (filters?: object) => ['orgs', 'list', filters]            as const,
  detail:     (id: string)       => ['orgs', 'detail', id]               as const,
  health:     (id: string)       => ['orgs', 'health', id]               as const,
  missions:   (orgId: string, f?: object) => ['orgs', orgId, 'missions', f] as const,
  mission:    (orgId: string, mid: string) => ['orgs', orgId, 'mission', mid] as const,
  tasks:      (orgId: string, f?: object) => ['orgs', orgId, 'tasks', f]    as const,
  events:     (orgId: string)    => ['orgs', orgId, 'events']            as const,
  departments:(orgId: string)    => ['orgs', orgId, 'departments']       as const,
};

// ── Organization hooks ─────────────────────────────────────────────────────

export function useOrganizations(params?: { status?: string }) {
  return useQuery({
    queryKey:  orgKeys.list(params),
    queryFn:   () => orgApi.list(params),
    staleTime: 60_000,  // orgs don't change often
  });
}

export function useOrganization(orgId: string | null | undefined) {
  return useQuery({
    queryKey: orgKeys.detail(orgId!),
    queryFn:  () => orgApi.get(orgId!),
    enabled:  !!orgId,
    staleTime: 30_000,
  });
}

export function useOrgHealth(orgId: string | null | undefined) {
  return useQuery({
    queryKey:      orgKeys.health(orgId!),
    queryFn:       () => orgApi.health(orgId!),
    enabled:       !!orgId,
    staleTime:     30_000,
    refetchInterval: 60_000,  // refresh health every minute
  });
}

export function useCreateOrganization() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: CreateOrganizationRequest) => orgApi.create(req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: orgKeys.all() });
    },
  });
}

export function useUpdateOrganization(orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: UpdateOrganizationRequest) => orgApi.update(orgId, req),
    onMutate: async (req) => {
      await qc.cancelQueries({ queryKey: orgKeys.detail(orgId) });
      const prev = qc.getQueryData(orgKeys.detail(orgId));
      qc.setQueryData(orgKeys.detail(orgId), (old: Organization | undefined) =>
        old ? { ...old, ...req } : old
      );
      return { prev };
    },
    onError: (_, __, ctx) => qc.setQueryData(orgKeys.detail(orgId), ctx?.prev),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: orgKeys.detail(orgId) });
      qc.invalidateQueries({ queryKey: orgKeys.all() });
    },
  });
}

// ── Mission hooks ──────────────────────────────────────────────────────────

export function useMissions(
  orgId: string | null | undefined,
  filters?: { status?: string; priority?: string }
) {
  return useInfiniteQuery({
    queryKey:           orgKeys.missions(orgId!, filters),
    queryFn:            ({ pageParam }) =>
      orgApi.listMissions(orgId!, { ...filters, cursor: pageParam as string | undefined }),
    initialPageParam:   undefined as string | undefined,
    getNextPageParam:   (page) => page.cursor ?? undefined,
    enabled:            !!orgId,
    staleTime:          15_000,
  });
}

export function useMission(orgId: string, missionId: string | null | undefined) {
  return useQuery({
    queryKey: orgKeys.mission(orgId, missionId!),
    queryFn:  () => orgApi.getMission(orgId, missionId!),
    enabled:  !!missionId,
  });
}

export function useCreateMission(orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: CreateMissionRequest) => orgApi.createMission(orgId, req),
    onMutate: async (req) => {
      await qc.cancelQueries({ queryKey: orgKeys.missions(orgId) });
      const prev = qc.getQueryData(orgKeys.missions(orgId));
      // Optimistic: add temp mission immediately
      qc.setQueryData(orgKeys.missions(orgId), (old: any) => {
        if (!old) return old;
        const tempMission: Partial<OrgMission> = {
          id: `temp-${Date.now()}`,
          org_id: orgId,
          title: req.title,
          status: 'draft',
          priority: req.priority ?? 'medium',
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        };
        return {
          ...old,
          pages: old.pages.map((p: any, i: number) =>
            i === 0 ? { ...p, data: [tempMission, ...p.data] } : p
          ),
        };
      });
      return { prev };
    },
    onError: (_, __, ctx) => qc.setQueryData(orgKeys.missions(orgId), ctx?.prev),
    onSettled: () => qc.invalidateQueries({ queryKey: orgKeys.missions(orgId) }),
  });
}

export function useUpdateMissionStatus(orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ missionId, status }: { missionId: string; status: MissionStatus }) =>
      orgApi.updateMissionStatus(orgId, missionId, status),
    onSuccess: (updated) => {
      // Update detail cache immediately
      qc.setQueryData(orgKeys.mission(orgId, updated.id), updated);
      // Invalidate list to reflect new status
      qc.invalidateQueries({ queryKey: orgKeys.missions(orgId) });
    },
  });
}

// ── Department hooks ───────────────────────────────────────────────────────

export function useDepartments(orgId: string | null | undefined) {
  return useQuery({
    queryKey: orgKeys.departments(orgId!),
    queryFn:  () => orgApi.listDepartments(orgId!),
    enabled:  !!orgId,
    staleTime: 60_000,
  });
}

export function useCreateDepartment(orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: CreateDepartmentRequest) => orgApi.createDepartment(orgId, req),
    onSuccess: () => qc.invalidateQueries({ queryKey: orgKeys.departments(orgId) }),
  });
}

// ── Events hooks ───────────────────────────────────────────────────────────

export function useOrgEvents(orgId: string | null | undefined) {
  return useQuery({
    queryKey:      orgKeys.events(orgId!),
    queryFn:       () => orgApi.listEvents(orgId!),
    enabled:       !!orgId,
    staleTime:     10_000,
    refetchInterval: 30_000,  // live event feed
  });
}

// ── Alias hooks (used by new components) ──────────────────────────────────

/** Alias for useMission — used by MissionPage. */
export function useOrgMission(orgId: string, missionId: string | null | undefined) {
  return useMission(orgId, missionId);
}

/** Alias for useDepartments — used by OrgChart / DepartmentPage. */
export function useOrgDepartments(orgId: string | null | undefined) {
  return useDepartments(orgId);
}

// ── Task hooks ────────────────────────────────────────────────────────────

export function useOrgTasks(
  orgId: string | null | undefined,
  filters?: { mission_id?: string; status?: string; limit?: number },
) {
  return useQuery({
    queryKey: orgKeys.tasks(orgId!, filters),
    queryFn: () => {
      const qs = new URLSearchParams();
      if (filters?.mission_id) qs.set('mission_id', filters.mission_id);
      if (filters?.status) qs.set('status', filters.status);
      if (filters?.limit) qs.set('limit', String(filters.limit));
      return apiFetch<any>(`/v1/org/${orgId!}/tasks?${qs}`).catch(() => ({ data: [] }));
    },
    enabled: !!orgId,
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
}

export function useUpdateTaskStatus(orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ taskId, status }: { taskId: string; status: string }) =>
      apiFetch(`/v1/org/${orgId}/tasks/${taskId}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ status }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: orgKeys.tasks(orgId) });
    },
  });
}

// ── SSE hooks ─────────────────────────────────────────────────────────────

/** Subscribe to live org-level SSE event stream. */
export function useOrgStream(orgId: string, onEvent?: (ev: any) => void) {
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!orgId) return;
    const es = new EventSource(`/v1/org/${orgId}/events/stream`);
    setConnected(true);
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        onEvent?.(data);
      } catch { /* ignore malformed */ }
    };
    es.onerror = () => setConnected(false);
    return () => {
      es.close();
      setConnected(false);
    };
  }, [orgId, onEvent]);

  return { connected };
}

/** Subscribe to mission execution SSE stream. */
export function useMissionStream(orgId: string, missionId: string | null | undefined) {
  const [events, setEvents] = useState<any[]>([]);
  const qc = useQueryClient();

  useEffect(() => {
    if (!orgId || !missionId) return;
    const es = new EventSource(`/v1/org/${orgId}/missions/${missionId}/stream`);
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        setEvents(prev => [...prev.slice(-99), data]);
        // Refresh mission cache on completion
        if (data.type === 'mission_completed' || data.type === 'mission_failed') {
          qc.invalidateQueries({ queryKey: orgKeys.mission(orgId, missionId) });
        }
      } catch { /* ignore */ }
    };
    return () => es.close();
  }, [orgId, missionId, qc]);

  return events;
}

/** Hook for approval queue. */
export function useApprovals(orgId: string | null | undefined) {
  return useQuery({
    queryKey: ['approvals', orgId],
    queryFn: () =>
      apiFetch<any>(`/v1/org/${orgId!}/approvals`)
        .then(r => (Array.isArray(r) ? r : r?.data ?? []))
        .catch(() => []),
    enabled: !!orgId,
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
}
