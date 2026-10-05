/**
 * GET /rag/strategies client: readiness is per collection (RAPTOR and agentic
 * chunking need that collection's precomputed index).
 */
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api/client';

export interface RagStrategyInfo {
  id: string;
  name: string;
  available: boolean;
  unavailable_reason: string | null;
}

interface RagStrategiesResponse {
  collection_id?: string | null;
  strategies: RagStrategyInfo[];
}

export const DEFAULT_RAG_STRATEGY = 'hybrid';

const NEEDS_COLLECTION = 'collection_index_required';
const NOT_INDEXED = new Set(['requires RAPTOR indexing', 'requires agentic-chunking indexing']);

export function strategiesPath(collectionId: string | null): string {
  return collectionId
    ? `/rag/strategies?collection_id=${encodeURIComponent(collectionId)}`
    : '/rag/strategies';
}

export function useRagStrategies(collectionId: string | null) {
  return useQuery<RagStrategiesResponse>({
    queryKey: ['rag-strategies', collectionId],
    queryFn: () => apiFetch<RagStrategiesResponse>(
      strategiesPath(collectionId), undefined, { silenceServerErrorToast: true },
    ),
    staleTime: 30_000,
  });
}

/** Why a strategy cannot be picked, in words a user can act on. */
export function strategyNote(s: RagStrategyInfo): string | null {
  if (s.available) return null;
  if (s.unavailable_reason === NEEDS_COLLECTION) return 'needs a collection';
  if (s.unavailable_reason && NOT_INDEXED.has(s.unavailable_reason)) return 'collection not indexed for it';
  return 'unavailable';
}
