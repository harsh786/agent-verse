/** Embedding model picker for a knowledge collection (see ./collectionEmbedders). */
import { embedderLabel, type EmbedderOption } from './collectionEmbedders';

interface SelectProps {
  id?: string;
  testId?: string;
  value: string;
  onChange: (key: string) => void;
  options: EmbedderOption[];
  className?: string;
  disabled?: boolean;
}

/** The embedding model picker; renders a static label when there is nothing to choose. */
export function CollectionEmbedderSelect({
  id = 'collection-embedder', testId = 'collection-embedder-select', value, onChange, options, className, disabled,
}: SelectProps) {
  if (options.length === 0) {
    return <p className="px-3 py-2 text-sm text-muted-foreground">Deployment default embedder</p>;
  }
  const selected = options.find((o) => o.key === value);
  return (
    <>
      <select
        id={id}
        data-testid={testId}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className={className ?? 'w-full px-3 py-2 border border-border rounded-md text-sm bg-background'}
      >
        {options.map((o) => (
          <option key={o.key} value={o.key} disabled={!o.available} title={o.reason || undefined}>
            {embedderLabel(o)}
          </option>
        ))}
      </select>
      {selected && (
        <p data-testid={`${testId}-hint`} className={`mt-1 text-[11px] ${selected.available ? 'text-muted-foreground' : 'text-red-500'}`}>
          {selected.available
            ? `Every document and query of this collection is embedded with ${selected.model}`
              + (selected.chunk_table ? ` (stored in ${selected.chunk_table}).` : '.')
            : selected.reason}
        </p>
      )}
    </>
  );
}
