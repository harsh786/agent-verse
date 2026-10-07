/**
 * Per-collection embedders: a knowledge collection is bound to ONE embedding
 * model and its width (which selects the chunk table). GET /knowledge/embedders
 * lists what a collection can be bound to — the deployment default first, then
 * every configured Model Registry embedding model with its width; a model that
 * cannot embed a collection (no chunk table for its width, unknown width, no
 * credentials) is listed but disabled, with the reason.
 */
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api/client';

export interface EmbedderOption {
  key: string;
  provider: string;
  model: string;
  dimension: number | null;
  is_default: boolean;
  available: boolean;
  reason: string;
  source: 'default' | 'registry';
  chunk_table: string | null;
}

interface EmbeddersResponse {
  embedders?: EmbedderOption[];
  default_dimension?: number | null;
  supported_dimensions?: number[];
}

export const DEFAULT_EMBEDDER_KEY = 'default';

export function useCollectionEmbedders() {
  return useQuery<EmbeddersResponse>({
    queryKey: ['knowledge-embedders'],
    queryFn: () => apiFetch<EmbeddersResponse>('/knowledge/embedders', undefined, { silenceServerErrorToast: true }),
    staleTime: 30_000,
  });
}

/**
 * The options to offer: the default first, then the registry models that are
 * not the default model itself (choosing that one IS the default).
 */
export function embedderChoices(data: EmbeddersResponse | undefined): EmbedderOption[] {
  const all = Array.isArray(data?.embedders) ? data.embedders : [];
  const defaults = all.filter((o) => o.source === 'default');
  const others = all.filter((o) => o.source !== 'default' && !(o.is_default && defaults.length > 0));
  return [...defaults, ...others];
}

export function embedderLabel(o: EmbedderOption): string {
  const width = o.dimension ? ` · ${o.dimension}-d` : '';
  if (o.source === 'default') return `Default — ${o.model}${width}`;
  return `${o.model} (${o.provider})${width}${o.available ? '' : ' — unavailable'}`;
}
