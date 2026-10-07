/** Registry row helpers shared by the page and its sections (Fast Refresh: no components here). */
import type { CapabilityGroup, ConfiguredModel } from '@/lib/api/client';

export interface CapabilityMeta {
  key: string;
  label: string;
  hint: string;
  /** Lower-case noun for empty states ("OCR model", "reranker"). */
  noun: string;
}

export const keyOf = (m: ConfiguredModel) => m.key || `${m.provider}/${m.model_id}`;

/** Whether the model can serve now (older backends send no `servable`). */
export const usable = (m: ConfiguredModel) => m.provider_ready && m.servable !== false;

/** A model that may run first: usable and not refused (e.g. a dimension mismatch). */
export const canLead = (m: ConfiguredModel) => usable(m) && !m.refused;

/** Why a model is skipped at runtime (and cannot be primary), or null. */
export function skipReason(m: ConfiguredModel): string | null {
  if (m.refused) return m.refusal_reason || 'Refused for this deployment';
  if (!m.provider_ready) return 'No API key: its provider has no key and the entry has no key or endpoint URL';
  if (m.servable === false) return 'Not served: nothing is configured to call it (add an endpoint URL or API key)';
  return null;
}

export type CoverageState = 'ready' | 'not_ready' | 'none';

/**
 * The truth per capability, from the registry rows themselves: ready when a
 * model can serve it now, not usable when models are listed but none can, not
 * configured when the capability has no model at all.
 */
export const coverageOf = (group: CapabilityGroup | undefined): { state: CoverageState; ready: number } => {
  if (!group || group.models.length === 0) return { state: 'none', ready: 0 };
  const ready = group.ready_count ?? group.models.filter((m) => canLead(m)).length;
  const state = group.status ?? (ready > 0 ? 'ready' : 'not_ready');
  return { state, ready };
};
