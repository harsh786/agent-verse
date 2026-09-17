import { useState } from 'react';
import { Clock, ChevronRight, Play, Pause, Trash2, RefreshCw, AlertCircle, CheckCircle, Zap } from 'lucide-react';
import type { SourceConfig } from '../types';
import { FAMILY_CONFIG } from '../types';
import { useSourceHealth, useTriggerSync, useDeleteSource, useUpdateSource } from '../hooks';
import { SourceDetailDrawer } from './SourceDetailDrawer';

interface SourceCardProps { source: SourceConfig; }

const STATUS_COLORS = {
  syncing:   'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
  error:     'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300',
  healthy:   'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300',
  disabled:  'bg-muted text-muted-foreground',
  checking:  'bg-muted text-muted-foreground',
};

export function SourceCard({ source }: SourceCardProps) {
  const [showDetail, setShowDetail] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const { data: health, isLoading: healthLoading, isError: healthErrored } = useSourceHealth(source.source_id, source.enabled);
  const sync   = useTriggerSync();
  const del    = useDeleteSource();
  const update = useUpdateSource();

  const familyCfg = FAMILY_CONFIG[source.family];
  const statusKey = !source.enabled ? 'disabled'
    : (healthErrored || health?.ok === false) ? 'error'
    : healthLoading ? 'checking'
    : 'healthy';

  function handleToggleEnabled(e: React.MouseEvent) {
    e.stopPropagation();
    update.mutate({ id: source.source_id, data: { enabled: !source.enabled } });
  }

  function handleSync(e: React.MouseEvent) {
    e.stopPropagation();
    sync.mutate(source.source_id);
  }

  function handleDelete(e: React.MouseEvent) {
    e.stopPropagation();
    if (!confirmDelete) { setConfirmDelete(true); setTimeout(() => setConfirmDelete(false), 3000); return; }
    del.mutate(source.source_id);
  }

  return (
    <>
      <div
        role="article"
        aria-label={`${source.name} source, ${statusKey}`}
        onClick={() => setShowDetail(true)}
        className="flex items-center gap-4 px-4 py-3 hover:bg-muted/30 cursor-pointer transition-colors group"
      >
        {/* Family icon */}
        <div className={`shrink-0 w-9 h-9 rounded-lg flex items-center justify-center text-${familyCfg.color} bg-${familyCfg.color}/10`}>
          <span className="text-lg" aria-hidden>{familyCfg.icon === 'Cloud' ? '☁' : '📄'}</span>
        </div>

        {/* Main content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium truncate">{source.name}</span>
            <span className="rounded bg-muted text-muted-foreground text-xs px-1.5 py-0.5 font-mono">
              {source.source_type}
            </span>
            <HealthBadge statusKey={statusKey} health={health} />
          </div>
          <div className="flex items-center gap-3 mt-0.5 text-xs text-muted-foreground">
            <span>{familyCfg.label}</span>
            {source.last_synced_at && (
              <span className="flex items-center gap-1">
                <Clock className="h-3 w-3" />
                {new Date(source.last_synced_at).toLocaleString()}
              </span>
            )}
            <span>{source.total_docs_indexed.toLocaleString()} docs</span>
            <span>{source.total_chunks.toLocaleString()} chunks</span>
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity" onClick={e => e.stopPropagation()}>
          <ActionBtn onClick={handleSync} disabled={sync.isPending || !source.enabled} title="Sync now" aria-label="Sync source now">
            <RefreshCw className={`h-3.5 w-3.5 ${sync.isPending ? 'animate-spin' : ''}`} />
          </ActionBtn>
          <ActionBtn onClick={handleToggleEnabled} disabled={update.isPending} title={source.enabled ? 'Disable' : 'Enable'} aria-label={source.enabled ? 'Disable source' : 'Enable source'}>
            {source.enabled ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
          </ActionBtn>
          <ActionBtn onClick={handleDelete} disabled={del.isPending}
            className={confirmDelete ? 'text-destructive hover:bg-destructive/10' : ''}
            title={confirmDelete ? 'Click again to confirm' : 'Delete'}
            aria-label="Delete source"
          >
            {confirmDelete ? <AlertCircle className="h-3.5 w-3.5" /> : <Trash2 className="h-3.5 w-3.5" />}
          </ActionBtn>
        </div>

        <ChevronRight className="h-4 w-4 text-muted-foreground shrink-0 group-hover:translate-x-0.5 transition-transform" />
      </div>

      {showDetail && (
        <SourceDetailDrawer source={source} onClose={() => setShowDetail(false)} />
      )}
    </>
  );
}

function HealthBadge({ statusKey, health }: { statusKey: string; health?: { ok: boolean; latency_ms: number } | null }) {
  const cls = STATUS_COLORS[statusKey as keyof typeof STATUS_COLORS] ?? STATUS_COLORS.healthy;
  const label = statusKey === 'healthy'  ? `Connected (${Math.round(health?.latency_ms ?? 0)}ms)` :
                statusKey === 'checking' ? 'Checking…' :
                statusKey === 'error'    ? 'Connection error' :
                statusKey === 'syncing'  ? 'Syncing…' : 'Disabled';
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${cls}`}
      aria-label={`Status: ${statusKey}`}
    >
      {statusKey === 'healthy'  ? <CheckCircle className="h-3 w-3" /> :
       statusKey === 'checking' ? <Clock className="h-3 w-3 animate-pulse" /> :
       statusKey === 'syncing'  ? <Zap className="h-3 w-3 animate-pulse" /> :
       <AlertCircle className="h-3 w-3" />}
      {label}
    </span>
  );
}

function ActionBtn({ onClick, disabled, children, title, className, 'aria-label': ariaLabel }: {
  onClick: (e: React.MouseEvent) => void;
  disabled?: boolean;
  children: React.ReactNode;
  title?: string;
  className?: string;
  'aria-label'?: string;
}) {
  return (
    <button
      onClick={onClick} disabled={disabled} title={title} aria-label={ariaLabel}
      className={`rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors disabled:opacity-50 ${className ?? ''}`}
    >
      {children}
    </button>
  );
}
