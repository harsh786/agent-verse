/**
 * TanStack Query hooks for the trigger system.
 */
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api/client';
import type {
  Trigger,
  CreateTriggerRequest,
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
  });
}

export function useRetryDLQEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (dlqId: string) =>
      apiFetch<void>(`/triggers/dlq/${dlqId}/retry`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: TRIGGER_KEYS.dlq() }),
  });
}
