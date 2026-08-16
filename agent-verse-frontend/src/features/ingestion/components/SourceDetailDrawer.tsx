import { X, RefreshCw, Activity, FileText } from 'lucide-react';
import { useState } from 'react';
import type { SourceConfig } from '../types';
import { FAMILY_CONFIG } from '../types';
import { useSourceHealth, useSyncStatus, useDocuments, useTriggerSync } from '../hooks';

interface Props { source: SourceConfig; onClose: () => void; }
type Tab = 'overview' | 'documents' | 'history' | 'settings';

export function SourceDetailDrawer({ source, onClose }: Props) {
  const [tab, setTab] = useState<Tab>('overview');
  const { data: health } = useSourceHealth(source.source_id);
  const { data: syncStatus } = useSyncStatus(source.source_id);
  const triggerSync = useTriggerSync();
  const familyCfg = FAMILY_CONFIG[source.family];

  return (
    <div className="fixed inset-0 z-50 flex" role="dialog" aria-modal="true" aria-label={`${source.name} source detail`}>
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative ml-auto flex h-full w-full max-w-xl flex-col bg-background shadow-xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <div>
            <div className="flex items-center gap-2">
              <span className="rounded bg-muted px-2 py-0.5 text-xs font-mono">{source.source_type}</span>
              <span className="text-xs text-muted-foreground">{familyCfg.label}</span>
              {health && (
                <span className={`text-xs ${health.ok ? 'text-emerald-600' : 'text-red-600'}`}>
                  {health.ok ? `● ${Math.round(health.latency_ms)}ms` : '✕ Error'}
                </span>
              )}
            </div>
            <h2 className="mt-1 text-base font-semibold">{source.name}</h2>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => triggerSync.mutate(source.source_id)}
              disabled={triggerSync.isPending}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted transition-colors disabled:opacity-50"
              aria-label="Sync source"
            >
              <RefreshCw className={`h-3 w-3 ${triggerSync.isPending ? 'animate-spin' : ''}`} />
              {triggerSync.isPending ? 'Syncing…' : 'Sync Now'}
            </button>
            <button onClick={onClose} aria-label="Close" className="rounded-md p-2 hover:bg-muted transition-colors">
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-border">
          {(['overview', 'documents', 'history', 'settings'] as Tab[]).map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                tab === t ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              {t.charAt(0).toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {tab === 'overview' && <OverviewTab source={source} syncStatus={syncStatus} />}
          {tab === 'documents' && <DocumentsTab sourceId={source.source_id} />}
          {tab === 'history' && <HistoryTab syncStatus={syncStatus} />}
          {tab === 'settings' && <SettingsTab source={source} />}
        </div>
      </div>
    </div>
  );
}

function OverviewTab({ source, syncStatus }: { source: SourceConfig; syncStatus: unknown }) {
  return (
    <div className="space-y-4">
      <Section title="Stats">
        <dl className="grid grid-cols-2 gap-2 text-sm">
          <dt className="text-muted-foreground">Docs indexed</dt>
          <dd className="font-medium">{source.total_docs_indexed.toLocaleString()}</dd>
          <dt className="text-muted-foreground">Chunks</dt>
          <dd className="font-medium">{source.total_chunks.toLocaleString()}</dd>
          <dt className="text-muted-foreground">Last synced</dt>
          <dd>{source.last_synced_at ? new Date(source.last_synced_at).toLocaleString() : 'Never'}</dd>
          <dt className="text-muted-foreground">Sync mode</dt>
          <dd className="capitalize">{source.sync_mode}</dd>
          <dt className="text-muted-foreground">Collection</dt>
          <dd className="font-mono text-xs">{source.collection_id || '—'}</dd>
        </dl>
      </Section>
        {(syncStatus as { status?: string } | null)?.status === 'running' && (
        <div className="rounded-lg border border-blue-200 bg-blue-50 dark:bg-blue-900/20 dark:border-blue-800 p-3 text-sm">
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-blue-500 animate-pulse" />
            <span className="font-medium text-blue-700 dark:text-blue-300">Sync in progress</span>
          </div>
        </div>
      )}
    </div>
  );
}

function DocumentsTab({ sourceId }: { sourceId: string }) {
  const { data: docs = [], isLoading } = useDocuments(sourceId);
  if (isLoading) return <div className="h-32 rounded-lg bg-muted animate-pulse" />;
  if (!docs.length) return (
    <div className="flex flex-col items-center py-10 text-muted-foreground">
      <FileText className="h-8 w-8 mb-2 opacity-30" />
      <p className="text-sm">No documents indexed yet.</p>
    </div>
  );
  return (
    <div className="space-y-1.5">
      {docs.map((doc: { id: string; title: string; chunk_count: number; quality_score: number; ingested_at: string }) => (
        <div key={doc.id} className="flex items-start gap-3 rounded-lg border border-border/50 px-3 py-2 text-xs">
          <FileText className="h-3.5 w-3.5 text-muted-foreground shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0">
            <p className="font-medium truncate">{doc.title || doc.id}</p>
            <p className="text-muted-foreground">{doc.chunk_count} chunks · score {doc.quality_score.toFixed(2)}</p>
          </div>
          <time className="text-muted-foreground shrink-0">{new Date(doc.ingested_at).toLocaleDateString()}</time>
        </div>
      ))}
    </div>
  );
}

function HistoryTab({ syncStatus }: { syncStatus: unknown }) {
  if (!syncStatus) return <p className="text-sm text-muted-foreground text-center py-8">No sync history yet.</p>;
  const job = syncStatus as Record<string, unknown>;
  return (
    <div className="space-y-2">
      <div className="rounded-lg border border-border p-3 text-sm">
        <div className="flex items-center gap-2 mb-2">
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${
            job.status === 'completed' ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300' :
            job.status === 'failed' ? 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300' :
            'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300'
          }`}>{String(job.status)}</span>
          <span className="text-muted-foreground text-xs">{String(job.sync_mode)} sync</span>
        </div>
        <dl className="grid grid-cols-2 gap-1 text-xs">
          <dt className="text-muted-foreground">Indexed</dt><dd>{String(job.docs_indexed ?? 0)}</dd>
          <dt className="text-muted-foreground">Skipped</dt><dd>{String(job.docs_skipped ?? 0)}</dd>
          <dt className="text-muted-foreground">Failed</dt><dd>{String(job.docs_failed ?? 0)}</dd>
          <dt className="text-muted-foreground">Chunks</dt><dd>{String(job.chunks_created ?? 0)}</dd>
        </dl>
        {Boolean(job.error_message) && (
          <p className="mt-2 text-xs text-destructive">{String(job.error_message)}</p>
        )}
      </div>
    </div>
  );
}

function SettingsTab({ source }: { source: SourceConfig }) {
  return (
    <div className="space-y-3">
      <Section title="Configuration">
        <dl className="grid grid-cols-2 gap-2 text-xs">
          <dt className="text-muted-foreground">Chunking</dt><dd>{source.chunking_strategy}</dd>
          <dt className="text-muted-foreground">Embedding</dt><dd>{source.embedding_model}</dd>
          <dt className="text-muted-foreground">PII action</dt><dd className="capitalize">{source.pii_action}</dd>
          <dt className="text-muted-foreground">Min quality</dt><dd>{source.min_quality_score}</dd>
          <dt className="text-muted-foreground">ACL inherit</dt><dd>{source.inherit_source_acl ? 'Yes' : 'No'}</dd>
        </dl>
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">{title}</h3>
      {children}
    </div>
  );
}
