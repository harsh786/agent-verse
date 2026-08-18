import { useState } from 'react';
import { Plus, Database, AlertCircle, RefreshCw } from 'lucide-react';
import { SourceList } from './components/SourceList';
import { SourceCreateWizard } from './components/SourceCreateWizard';
import { QuotaUsageBar } from './components/QuotaUsageBar';
import { useSources, useIngestionQuota } from './hooks';
import type { SourceFamily } from './types';
import { FAMILY_CONFIG } from './types';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';

export function SourcesPage() {
  const [showCreate, setShowCreate] = useState(false);
  const [familyFilter, setFamilyFilter] = useState<SourceFamily | 'all'>('all');

  const { data: sources = [], isLoading, isError, refetch } = useSources();
  const { data: quota } = useIngestionQuota();

  const active   = sources.filter(s => s.enabled).length;
  const syncing  = 0; // real-time from SSE

  return (
    <JARVISPageShell>
    <JARVISStagger className="flex flex-col gap-6 p-6 max-w-screen-xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Database className="h-6 w-6 text-sky-500" />
            Knowledge Sources
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Connect any data source. Agents retrieve knowledge from all of them.
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="inline-flex items-center gap-2 rounded-lg bg-primary text-primary-foreground px-4 py-2 text-sm font-medium hover:bg-primary/90 transition-colors"
        >
          <Plus className="h-4 w-4" />
          Add Source
        </button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard label="Total Sources" value={sources.length} icon="📚" />
        <StatCard label="Active"  value={active}           icon="🟢" />
        <StatCard label="Syncing" value={syncing}           icon="🔄" />
        <StatCard label="Families" value={new Set(sources.map(s => s.family)).size} icon="🗂" />
      </div>

      {/* Quota bar */}
      {quota && <QuotaUsageBar quota={quota} />}

      {/* Family filter chips */}
      <div className="flex flex-wrap gap-2">
        <FamilyChip family="all" active={familyFilter === 'all'} label="All" onClick={() => setFamilyFilter('all')} />
        {(Object.keys(FAMILY_CONFIG) as SourceFamily[]).map(f => (
          <FamilyChip
            key={f}
            family={f}
            active={familyFilter === f}
            label={FAMILY_CONFIG[f].label}
            onClick={() => setFamilyFilter(familyFilter === f ? 'all' : f)}
            count={sources.filter(s => s.family === f).length}
          />
        ))}
      </div>

      {/* Error */}
      {isError && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive" role="alert">
          <AlertCircle className="h-4 w-4 shrink-0" />
          Failed to load sources.
          <button onClick={() => refetch()} className="ml-auto underline flex items-center gap-1">
            <RefreshCw className="h-3 w-3" />Retry
          </button>
        </div>
      )}

      {/* Source list */}
      <SourceList
        sources={familyFilter === 'all' ? sources : sources.filter(s => s.family === familyFilter)}
        isLoading={isLoading}
        onAddSource={() => setShowCreate(true)}
      />

      {/* Create wizard */}
      {showCreate && <SourceCreateWizard onClose={() => setShowCreate(false)} />}
    </JARVISStagger>
    </JARVISPageShell>
  );
}

function StatCard({ label, value, icon }: { label: string; value: number; icon: string }) {
  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{label}</span>
        <span className="text-base">{icon}</span>
      </div>
      <div className="mt-2 text-2xl font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function FamilyChip({
  family: _family, active, label, count, onClick,
}: {
  family: string; active: boolean; label: string; count?: number; onClick: () => void;
}) {
  return (
    <JARVISPageShell>
    <button
      onClick={onClick}
      role="checkbox"
      aria-checked={active}
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
        active
          ? 'bg-primary text-primary-foreground border-primary'
          : 'bg-background text-muted-foreground border-border hover:border-primary hover:text-foreground'
      }`}
    >
      {label}
      {count !== undefined && count > 0 && (
        <span className={`rounded-full px-1 ${active ? 'bg-primary-foreground/20' : 'bg-muted'}`}>
          {count}
        </span>
      )}
    </button>
    </JARVISPageShell>
  );
}
