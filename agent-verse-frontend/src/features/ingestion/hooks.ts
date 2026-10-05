/**
 * TanStack Query hooks for the ingestion system.
 * Covers all 38 API endpoints.
 */
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api/client';
import type {
  SourceConfig,
  IngestionJob,
  ConnectionHealth,
  IndexedDocument,
  DLQEntry,
  IngestionQuota,
  ConnectorMeta,
  SourcePreview,
  SourceValidation,
} from './types';

// ── Query keys ────────────────────────────────────────────────────────────────
export const INGESTION_KEYS = {
  sources:     () => ['ingestion', 'sources'] as const,
  source:      (id: string) => ['ingestion', 'source', id] as const,
  health:      (id: string) => ['ingestion', 'health', id] as const,
  syncStatus:  (id: string) => ['ingestion', 'sync', id] as const,
  documents:   (sourceId: string) => ['ingestion', 'documents', sourceId] as const,
  dlq:         () => ['ingestion', 'dlq'] as const,
  quota:       () => ['ingestion', 'quota'] as const,
  cost:        () => ['ingestion', 'cost'] as const,
  catalogue:   () => ['ingestion', 'catalogue'] as const,
};

// ── Sources ───────────────────────────────────────────────────────────────────

export function useSources() {
  return useQuery({
    queryKey: INGESTION_KEYS.sources(),
    queryFn: () => apiFetch<SourceConfig[]>('/sources'),
    staleTime: 30_000,
  });
}

export function useSource(sourceId: string) {
  return useQuery({
    queryKey: INGESTION_KEYS.source(sourceId),
    queryFn: () => apiFetch<SourceConfig>(`/sources/${sourceId}`),
    enabled: !!sourceId,
  });
}

/** LAW-21: a healthy source is re-checked every 5 minutes while it is watched. */
export const HEALTH_POLL_BASE_MS = 5 * 60 * 1000;
export const HEALTH_POLL_MAX_MS = 60 * 60 * 1000;

/** Poll interval after `consecutiveFailures` failed checks: 5, 10, 20, 40, then 60 min. */
export function healthPollInterval(consecutiveFailures: number): number {
  return Math.min(HEALTH_POLL_BASE_MS * 2 ** Math.max(0, consecutiveFailures), HEALTH_POLL_MAX_MS);
}

// Consecutive failed checks per source in this tab (an {ok:false} answer or a
// failed request) — UI pacing only, so it lives with the query client.
const healthFailures = new Map<string, number>();

/**
 * GET /sources/{id}/health. Every call makes the backend open a real
 * connection (discovery + ping + listCollections for MongoDB), so (C8):
 * - no retries: the app default (3) turned one failing check into 4 connections;
 * - no refetch on window focus;
 * - `poll` (the open detail drawer only) re-checks on an interval that backs
 *   off while the source keeps failing and pauses while the tab is hidden.
 *   A list card checks once (cached for staleTime) and does not poll.
 */
export function useSourceHealth(sourceId: string, enabled = true, { poll = false }: { poll?: boolean } = {}) {
  return useQuery({
    queryKey: INGESTION_KEYS.health(sourceId),
    queryFn: async () => {
      try {
        const health = await apiFetch<ConnectionHealth>(`/sources/${sourceId}/health`);
        healthFailures.set(sourceId, health.ok ? 0 : (healthFailures.get(sourceId) ?? 0) + 1);
        return health;
      } catch (e) {
        healthFailures.set(sourceId, (healthFailures.get(sourceId) ?? 0) + 1);
        throw e;
      }
    },
    enabled: !!sourceId && enabled,
    retry: false,
    refetchOnWindowFocus: false,
    refetchIntervalInBackground: false,
    refetchInterval: poll
      ? () =>
          typeof document !== 'undefined' && document.visibilityState === 'hidden'
            ? false
            : healthPollInterval(healthFailures.get(sourceId) ?? 0)
      : false,
    staleTime: 4 * 60 * 1000,
  });
}

export function useCreateSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<SourceConfig>) =>
      apiFetch<SourceConfig>('/sources', { method: 'POST', body: JSON.stringify(data) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: INGESTION_KEYS.sources() }),
  });
}

/** POST /sources/validate — check an UNSAVED config (and its connection) before create. */
export function useValidateSource() {
  return useMutation({
    mutationFn: (data: Partial<SourceConfig>) =>
      apiFetch<SourceValidation>('/sources/validate?check_connection=true', { method: 'POST', body: JSON.stringify(data) }),
  });
}

export function useUpdateSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<SourceConfig> }) =>
      apiFetch<SourceConfig>(`/sources/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
    onSuccess: (_d, { id }) => {
      qc.invalidateQueries({ queryKey: INGESTION_KEYS.sources() });
      qc.invalidateQueries({ queryKey: INGESTION_KEYS.source(id) });
    },
  });
}

export function useDeleteSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/sources/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: INGESTION_KEYS.sources() }),
  });
}

// ── Sync control ──────────────────────────────────────────────────────────────

/** Job id a sync was queued with, per source, until the worker reports it finished. */
const pendingSyncKey = (sourceId: string) => ['ingestion', 'pending-sync', sourceId] as const;
const TERMINAL_SYNC = new Set(['completed', 'failed', 'partial', 'cancelled']);

function sameJob(a: unknown, b: unknown): boolean {
  return String(a ?? '').replace(/-/g, '').toLowerCase() === String(b ?? '').replace(/-/g, '').toLowerCase();
}

export function useTriggerSync() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sourceId: string) =>
      apiFetch<{ status: string; job_id?: string }>(`/sources/${sourceId}/sync`, { method: 'POST' }),
    onSuccess: (data, sourceId) => {
      // The API answers before the worker has created the job: follow THIS job
      // (P1b-9). Invalidating once read the previous job — or "never_synced" —
      // and polling stopped, so the drawer never showed the sync it started.
      if (data?.job_id) qc.setQueryData(pendingSyncKey(sourceId), data.job_id);
      qc.invalidateQueries({ queryKey: INGESTION_KEYS.syncStatus(sourceId) });
    },
  });
}

export function useSyncStatus(sourceId: string) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: INGESTION_KEYS.syncStatus(sourceId),
    queryFn: async () => {
      const job = await apiFetch<IngestionJob>(`/sources/${sourceId}/sync/status`);
      const pending = qc.getQueryData<string>(pendingSyncKey(sourceId));
      if (!pending) return job;
      if (!sameJob(job?.job_id, pending)) {
        // Not started yet: show the queued job instead of the previous one.
        return { ...job, job_id: pending, status: 'pending', error_message: '' } as IngestionJob;
      }
      if (TERMINAL_SYNC.has(String(job.status))) {
        qc.removeQueries({ queryKey: pendingSyncKey(sourceId) });
        // Counts, documents and history changed with the finished run.
        void qc.invalidateQueries({ queryKey: INGESTION_KEYS.sources() });
        void qc.invalidateQueries({ queryKey: INGESTION_KEYS.documents(sourceId) });
        void qc.invalidateQueries({ queryKey: INGESTION_KEYS.dlq() });
      }
      return job;
    },
    enabled: !!sourceId,
    refetchInterval: (query) => {
      const status = String(query.state.data?.status ?? '');
      return status === 'running' || status === 'pending' ? 3000 : false; // until it finishes
    },
  });
}

export function useSyncHistory(sourceId: string, limit = 20) {
  return useQuery({
    queryKey: [...INGESTION_KEYS.syncStatus(sourceId), 'history', limit] as const,
    queryFn: () => apiFetch<IngestionJob[]>(`/sources/${sourceId}/sync/history?limit=${limit}`),
    enabled: !!sourceId,
  });
}

export function useCancelSync() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sourceId: string) =>
      apiFetch<{ status: string; job_id: string }>(`/sources/${sourceId}/sync/cancel`, { method: 'POST' }),
    onSuccess: (_d, sourceId) => {
      qc.invalidateQueries({ queryKey: INGESTION_KEYS.syncStatus(sourceId) });
    },
  });
}

/** Delete everything the source indexed and re-sync it from the start. */
export function useReindexSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sourceId: string) =>
      apiFetch<{ status: string; job_id: string }>(`/sources/${sourceId}/reindex`, { method: 'POST' }),
    onSuccess: (_d, sourceId) => {
      qc.invalidateQueries({ queryKey: INGESTION_KEYS.syncStatus(sourceId) });
      qc.invalidateQueries({ queryKey: INGESTION_KEYS.documents(sourceId) });
    },
  });
}

// ── Preview ───────────────────────────────────────────────────────────────────

/** POST /sources/{id}/preview — dry-run parse + chunk of the first documents (needs a saved source). */
export function useSourcePreview() {
  return useMutation({
    mutationFn: (sourceId: string) =>
      apiFetch<SourcePreview>(`/sources/${sourceId}/preview`, { method: 'POST' }),
  });
}

// ── Documents ─────────────────────────────────────────────────────────────────

export function useDocuments(sourceId: string, limit = 50) {
  return useQuery({
    queryKey: INGESTION_KEYS.documents(sourceId),
    queryFn: () => apiFetch<IndexedDocument[]>(`/ingestion/documents?source_id=${sourceId}&limit=${limit}`),
    enabled: !!sourceId,
  });
}

// ── DLQ ───────────────────────────────────────────────────────────────────────

export function useIngestionDLQ() {
  return useQuery({
    queryKey: INGESTION_KEYS.dlq(),
    queryFn: () => apiFetch<DLQEntry[]>('/ingestion/dlq'),
    refetchInterval: 60_000,
  });
}

/** Replay one DLQ entry now (POST /ingestion/dlq/{id}/retry, queued on the worker). */
export function useRetryDLQEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (dlqId: string) =>
      apiFetch<{ status: string; dlq_id: string }>(`/ingestion/dlq/${dlqId}/retry`, { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: INGESTION_KEYS.dlq() });
    },
  });
}

// ── Quota & Cost ──────────────────────────────────────────────────────────────

export function useIngestionQuota() {
  return useQuery({
    queryKey: INGESTION_KEYS.quota(),
    queryFn: () => apiFetch<IngestionQuota>('/ingestion/quota'),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
}

export function useIngestionCost() {
  return useQuery({
    queryKey: INGESTION_KEYS.cost(),
    queryFn: () => apiFetch<Record<string, unknown>>('/ingestion/cost'),
    staleTime: 5 * 60 * 1000,
  });
}

// ── Catalogue ─────────────────────────────────────────────────────────────────

export function useConnectorCatalogue() {
  return useQuery({
    queryKey: INGESTION_KEYS.catalogue(),
    queryFn: () => apiFetch<ConnectorMeta[]>('/sources/catalogue'),
    staleTime: Infinity, // catalogue rarely changes
  });
}
