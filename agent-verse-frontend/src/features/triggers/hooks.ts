/**
 * TanStack Query hooks for the trigger system.
 */
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api/client';
import { toast } from '@/stores/toast';
import type {
  Trigger,
  CreateTriggerRequest,
  UpdateTriggerRequest,
  TriggerEvent,
  TriggerDLQEntry,
  SimulationResult,
} from './types';

// ── Query Keys ────────────────────────────────────────────────────────────────

export const TRIGGER_KEYS = {
  all: ['triggers'] as const,
  list: () => [...TRIGGER_KEYS.all, 'list'] as const,
  detail: (id: string) => [...TRIGGER_KEYS.all, 'detail', id] as const,
  events: (id: string) => [...TRIGGER_KEYS.all, 'events', id] as const,
  dlq: () => [...TRIGGER_KEYS.all, 'dlq'] as const,
};

// ── Queries ───────────────────────────────────────────────────────────────────

export function useTriggers() {
  return useQuery({
    queryKey: TRIGGER_KEYS.list(),
    queryFn: () => apiFetch<Trigger[]>('/triggers'),
    staleTime: 30_000,
  });
}

export function useTrigger(scheduleId: string) {
  return useQuery({
    queryKey: TRIGGER_KEYS.detail(scheduleId),
    queryFn: () => apiFetch<Trigger>(`/triggers/${scheduleId}`),
    enabled: !!scheduleId,
  });
}

/** Goal-outcome circuit of a trigger (TRG-13): open after repeated failed goals. */
export interface TriggerCircuit {
  state: 'closed' | 'open' | 'half_open' | 'unknown';
  consecutive_failures: number;
  retry_at: string | null;
}

export function useTriggerCircuit(scheduleId: string) {
  return useQuery({
    queryKey: [...TRIGGER_KEYS.detail(scheduleId), 'circuit'] as const,
    queryFn: () => apiFetch<TriggerCircuit>(`/schedules/${scheduleId}/circuit`),
    enabled: !!scheduleId,
    retry: false,
    refetchInterval: 30_000,
  });
}

export function useTriggerEvents(scheduleId: string, limit = 50) {
  return useQuery({
    queryKey: TRIGGER_KEYS.events(scheduleId),
    queryFn: () => apiFetch<TriggerEvent[]>(`/triggers/${scheduleId}/events?limit=${limit}`),
    enabled: !!scheduleId,
    refetchInterval: 15_000,
  });
}

export function useTriggerDLQ() {
  return useQuery({
    queryKey: TRIGGER_KEYS.dlq(),
    queryFn: () => apiFetch<TriggerDLQEntry[]>('/triggers/dlq'),
    refetchInterval: 60_000,
  });
}

// ── Mutations ─────────────────────────────────────────────────────────────────

export function useCreateTrigger() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: CreateTriggerRequest) =>
      apiFetch<Trigger>('/triggers', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: TRIGGER_KEYS.list() }),
  });
}

export function useUpdateTrigger() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ scheduleId, data }: { scheduleId: string; data: UpdateTriggerRequest }) =>
      apiFetch<Trigger>(`/triggers/${scheduleId}`, {
        method: 'PATCH',
        body: JSON.stringify(data),
      }),
    onSuccess: (_data, { scheduleId }) => {
      qc.invalidateQueries({ queryKey: TRIGGER_KEYS.list() });
      qc.invalidateQueries({ queryKey: TRIGGER_KEYS.detail(scheduleId) });
    },
  });
}

export function usePauseTrigger() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (scheduleId: string) =>
      apiFetch<void>(`/triggers/${scheduleId}/pause`, { method: 'POST' }),
    onSuccess: (_data, scheduleId) => {
      qc.invalidateQueries({ queryKey: TRIGGER_KEYS.list() });
      qc.invalidateQueries({ queryKey: TRIGGER_KEYS.detail(scheduleId) });
    },
  });
}

export function useResumeTrigger() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (scheduleId: string) =>
      apiFetch<void>(`/triggers/${scheduleId}/resume`, { method: 'POST' }),
    onSuccess: (_data, scheduleId) => {
      qc.invalidateQueries({ queryKey: TRIGGER_KEYS.list() });
      qc.invalidateQueries({ queryKey: TRIGGER_KEYS.detail(scheduleId) });
    },
  });
}

export function useDeleteTrigger() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (scheduleId: string) =>
      apiFetch<void>(`/triggers/${scheduleId}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: TRIGGER_KEYS.list() }),
  });
}

export function useSimulateTrigger() {
  return useMutation({
    mutationFn: ({ scheduleId, payload }: { scheduleId: string; payload?: Record<string, unknown> }) =>
      apiFetch<SimulationResult>(`/triggers/${scheduleId}/simulate`, {
        method: 'POST',
        body: JSON.stringify({ payload }),
      }),
  });
}

export function useFireTriggerNow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ scheduleId, payload }: { scheduleId: string; payload?: Record<string, unknown> }) =>
      apiFetch<{ goal_id: string }>(`/triggers/${scheduleId}/fire`, {
        method: 'POST',
        body: JSON.stringify({ payload }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: TRIGGER_KEYS.list() }),
    onError: (err) => toast({ kind: 'error', message: fireErrorMessage(err) }),
  });
}

/** Human-readable reason a manual fire was refused (403 = the caller's role). */
export function fireErrorMessage(err: unknown): string {
  const status = (err as { status?: number } | null)?.status;
  const reason = err instanceof Error && err.message ? err.message : '';
  if (status === 403) {
    return reason || 'Your role is not permitted to fire triggers (operator or admin required).';
  }
  return reason ? `Fire failed: ${reason}` : 'Fire failed.';
}

export function useRetryDLQEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (dlqId: string) =>
      apiFetch<void>(`/triggers/dlq/${dlqId}/retry`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: TRIGGER_KEYS.dlq() }),
  });
}
