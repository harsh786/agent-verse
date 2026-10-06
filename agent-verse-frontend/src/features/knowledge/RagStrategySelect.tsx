/**
 * RAG strategy picker backed by GET /rag/strategies.
 *
 * Readiness is per collection: RAPTOR and agentic chunking only run on a
 * collection that carries their precomputed index, so the picker always asks
 * the backend with the selected `collection_id`. With no single collection
 * selected the backend reports `collection_index_required`, shown here as
 * "needs a collection" (not as broken).
 */
import { useEffect } from 'react';
import { DEFAULT_RAG_STRATEGY, strategyNote, useRagStrategies } from './ragStrategies';

interface Props {
  /** The one selected collection, or null when none (or several) are selected. */
  collectionId: string | null;
  value: string;
  onChange: (strategy: string) => void;
  id?: string;
  className?: string;
}

export function RagStrategySelect({ collectionId, value, onChange, id = 'rag-strategy', className }: Props) {
  const { data } = useRagStrategies(collectionId);
  const strategies = data?.strategies ?? [];
  const selected = strategies.find((s) => s.id === value);

  // A collection change can make the chosen strategy unavailable, and a saved
  // value can name a strategy the backend does not offer (older workflow
  // definitions stored made-up ids): fall back to the default instead of
  // sending a request that can only fail.
  const unknown = strategies.length > 0 && selected === undefined;
  useEffect(() => {
    if (value === DEFAULT_RAG_STRATEGY) return;
    if (unknown || (selected && !selected.available)) onChange(DEFAULT_RAG_STRATEGY);
  }, [selected, unknown, value, onChange]);

  const options = strategies.length > 0
    ? strategies
    : [{ id: DEFAULT_RAG_STRATEGY, name: 'Hybrid', available: true, unavailable_reason: null }];

  return (
    <select
      id={id}
      data-testid="rag-strategy-select"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={className ?? 'w-full px-3 py-2 border border-border rounded-md text-sm bg-background'}
    >
      {options.map((s) => {
        const note = strategyNote(s);
        return (
          <option key={s.id} value={s.id} disabled={!s.available} title={s.unavailable_reason ?? undefined}>
            {note ? `${s.name} (${note})` : s.name}
          </option>
        );
      })}
    </select>
  );
}
