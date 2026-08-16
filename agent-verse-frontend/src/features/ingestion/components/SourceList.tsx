import { Search, Database } from 'lucide-react';
import { useState } from 'react';
import type { SourceConfig } from '../types';
import { FAMILY_CONFIG } from '../types';
import { SourceCard } from './SourceCard';

interface SourceListProps {
  sources: SourceConfig[];
  isLoading: boolean;
  onAddSource: () => void;
}

export function SourceList({ sources, isLoading, onAddSource }: SourceListProps) {
  const [search, setSearch] = useState('');

  const filtered = sources.filter(s =>
    !search ||
    s.name.toLowerCase().includes(search.toLowerCase()) ||
    s.source_type.toLowerCase().includes(search.toLowerCase()) ||
    FAMILY_CONFIG[s.family]?.label.toLowerCase().includes(search.toLowerCase())
  );

  if (isLoading) {
    return (
      <div className="space-y-2" aria-busy="true" aria-label="Loading sources">
        {[1, 2, 3].map(i => <div key={i} className="h-16 rounded-xl bg-muted animate-pulse" />)}
      </div>
    );
  }

  if (sources.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center text-muted-foreground">
        <Database className="h-12 w-12 mb-4 opacity-20" />
        <p className="text-lg font-medium">No knowledge sources yet</p>
        <p className="text-sm mt-1">Connect a data source so your agents can retrieve knowledge from it.</p>
        <button
          onClick={onAddSource}
          className="mt-4 rounded-lg bg-primary text-primary-foreground px-4 py-2 text-sm font-medium hover:bg-primary/90 transition-colors"
        >
          Add your first source
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search sources…"
          aria-label="Search sources"
          className="w-full pl-9 pr-3 py-2 text-sm rounded-lg border border-border bg-background focus:outline-none focus:ring-2 focus:ring-ring"
        />
      </div>

      {filtered.length === 0 && (
        <p className="text-sm text-muted-foreground text-center py-8">
          No results for "{search}".{' '}
          <button onClick={() => setSearch('')} className="underline">Clear search</button>
        </p>
      )}

      {/* Sources grouped by family */}
      <div className="rounded-xl border border-border bg-card overflow-hidden divide-y divide-border">
        {filtered.map(source => (
          <SourceCard key={source.source_id} source={source} />
        ))}
      </div>
    </div>
  );
}
