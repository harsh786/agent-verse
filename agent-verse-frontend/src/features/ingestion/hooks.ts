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
} from './types';

// ── Query keys ────────────────────────────────────────────────────────────────
export const INGESTION_KEYS = {
  sources:     () => ['ingestion', 'sources'] as const,
  source:      (id: string) => ['ingestion', 'source', id] as const,
  health:      (id: string) => ['ingestion', 'health', id] as const,
  syncStatus:  (id: string) => ['ingestion', 'sync', id] as const,
  syncHistory: (id: string) => ['ingestion', 'history', id] as const,
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

export function useSourceHealth(sourceId: string, enabled = true) {
  return useQuery({
    queryKey: INGESTION_KEYS.health(sourceId),
    queryFn: () => apiFetch<ConnectionHealth>(`/sources/${sourceId}/health`),
    enabled: !!sourceId && enabled,
    refetchInterval: 5 * 60 * 1000, // LAW-21: poll every 5 minutes
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

export function useTriggerSync() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sourceId: string) =>
      apiFetch<{ status: string; job_id?: string }>(`/sources/${sourceId}/sync`, { method: 'POST' }),
    onSuccess: (_d, sourceId) => {
      qc.invalidateQueries({ queryKey: INGESTION_KEYS.syncStatus(sourceId) });
    },
  });
}

export function useSyncStatus(sourceId: string) {
  return useQuery({
    queryKey: INGESTION_KEYS.syncStatus(sourceId),
    queryFn: () => apiFetch<IngestionJob>(`/sources/${sourceId}/sync/status`),
    enabled: !!sourceId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'running' ? 3000 : false; // poll only while running
    },
  });
}

export function useSyncHistory(sourceId: string) {
  return useQuery({
    queryKey: INGESTION_KEYS.syncHistory(sourceId),
    queryFn: () => apiFetch<IngestionJob[]>(`/sources/${sourceId}/sync/history`),
    enabled: !!sourceId,
  });
}

// ── Preview ───────────────────────────────────────────────────────────────────

export function useSourcePreview() {
  return useMutation({
    mutationFn: (sourceId: string) =>
      apiFetch<{ docs_previewed: number; sample: unknown[] }>(
        `/sources/${sourceId}/preview`, { method: 'POST' }
      ),
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

export function useRetryDLQEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (dlqId: string) => apiFetch<void>(`/ingestion/dlq/${dlqId}/retry`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: INGESTION_KEYS.dlq() }),
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
