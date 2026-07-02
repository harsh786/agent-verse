/**
 * MemoryExplorerPage — world-class long-term memory management.
 *
 * Sections:
 *   1. Semantic Recall     — search bar, confidence bars, type badges, source links
 *   2. Long-term Memories  — type filter, Add Memory, Clear All, tags as chips, dates
 *   3. Tool Reliability    — fixed field names, color-coded rows, progress bars
 *   4. Execution Memory    — recent winning plans (new! was backend-only before)
 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Trash2, Search, Plus, Brain, Wrench, Cpu, X, Loader2,
  AlertTriangle, CheckCircle2, ChevronDown,
} from 'lucide-react';
import { memoryApi, type RecallResult, type MemoryEntry } from '@/lib/api/client';
import { toast } from '@/stores/toast';
import { Skeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { ConfirmModal } from '@/components/ui/ConfirmModal';

// ── Constants ────────────────────────────────────────────────────────────────

const MEMORY_TYPES = ['fact', 'skill', 'preference', 'tool_usage', 'goal_completion', 'observation'];

const TYPE_COLORS: Record<string, string> = {
  fact:             'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
  skill:            'bg-violet-100 text-violet-800 dark:bg-violet-900/30 dark:text-violet-300',
  preference:       'bg-pink-100 text-pink-800 dark:bg-pink-900/30 dark:text-pink-300',
  tool_usage:       'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
  goal_completion:  'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300',
  observation:      'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
};

function typeColor(t: string) {
  return TYPE_COLORS[t] ?? 'bg-muted text-muted-foreground';
}

function formatDate(iso: string) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return '';
  }
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = pct >= 80 ? 'bg-green-500' : pct >= 50 ? 'bg-amber-500' : 'bg-red-500';
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1.5 bg-muted rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] text-muted-foreground tabular-nums">{pct}%</span>
    </div>
  );
}

// ── Add Memory Modal ─────────────────────────────────────────────────────────

function AddMemoryModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [content, setContent] = useState('');
  const [memType, setMemType] = useState('fact');
  const [confidence, setConfidence] = useState(80);
  const [tags, setTags] = useState('');

  const mutation = useMutation({
    mutationFn: () =>
      memoryApi.create({
        content,
        memory_type: memType,
        confidence: confidence / 100,
        tags: tags ? tags.split(',').map((t) => t.trim()).filter(Boolean) : [],
      }),
    onSuccess: () => {
      toast({ kind: 'success', message: 'Memory created.' });
      onCreated();
      onClose();
    },
    onError: (e) => toast({ kind: 'error', message: String(e) }),
  });

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />
      <div className="relative bg-card border border-border rounded-xl shadow-xl max-w-md w-full p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold flex items-center gap-2">
            <Brain className="h-4 w-4 text-primary" aria-hidden="true" /> Add Memory
          </h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium mb-1" htmlFor="mem-content">
              Content <span className="text-red-500">*</span>
            </label>
            <textarea
              id="mem-content"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={3}
              placeholder="What should the agent remember?"
              className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary resize-none"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium mb-1" htmlFor="mem-type">Type</label>
              <select
                id="mem-type"
                value={memType}
                onChange={(e) => setMemType(e.target.value)}
                className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary"
              >
                {MEMORY_TYPES.map((t) => (
                  <option key={t} value={t} className="capitalize">{t.replace('_', ' ')}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium mb-1" htmlFor="mem-conf">
                Confidence: {confidence}%
              </label>
              <input
                id="mem-conf"
                type="range"
                min={10}
                max={100}
                step={5}
                value={confidence}
                onChange={(e) => setConfidence(Number(e.target.value))}
                className="w-full accent-primary"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium mb-1" htmlFor="mem-tags">
              Tags <span className="text-muted-foreground font-normal">(comma-separated)</span>
            </label>
            <input
              id="mem-tags"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="ops, api, deployment"
              className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary"
            />
          </div>
        </div>

        <div className="flex gap-3">
          <button
            onClick={() => mutation.mutate()}
            disabled={!content.trim() || mutation.isPending}
            className="flex-1 py-2.5 bg-primary text-primary-foreground text-sm font-medium rounded-lg hover:opacity-90 disabled:opacity-50"
          >
            {mutation.isPending ? 'Creating…' : 'Create Memory'}
          </button>
          <button onClick={onClose} className="px-4 py-2.5 border border-input text-sm rounded-lg hover:bg-muted/50">
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function MemoryExplorerPage() {
  const qc = useQueryClient();
  const [recallQuery, setRecallQuery] = useState('');
  const [recalled, setRecalled] = useState<RecallResult[] | null>(null);
  const [typeFilter, setTypeFilter] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [clearOpen, setClearOpen] = useState(false);
  const [execOpen, setExecOpen] = useState(false);

  // ── Queries ────────────────────────────────────────────────────────────────

  const { data: memories = [], isLoading } = useQuery({
    queryKey: ['memories', typeFilter],
    queryFn: () => memoryApi.list({ limit: 100, memoryType: typeFilter ?? undefined }),
  });

  const { data: reliability = [] } = useQuery({
    queryKey: ['tool-reliability'],
    queryFn: () => memoryApi.toolReliability(),
  });

  const { data: execMemories = [], isLoading: execLoading } = useQuery({
    queryKey: ['execution-memories'],
    queryFn: () => memoryApi.listExecution(),
    enabled: execOpen,
    staleTime: 30_000,
  });

  // ── Mutations ──────────────────────────────────────────────────────────────

  const recallMutation = useMutation({
    mutationFn: () => memoryApi.recall(recallQuery, 10),
    onSuccess: (rows) => setRecalled(rows),
    onError: (e) => toast({ kind: 'error', message: String(e) }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => memoryApi.delete(id),
    onSuccess: () => {
      toast({ kind: 'success', message: 'Memory deleted.' });
      qc.invalidateQueries({ queryKey: ['memories'] });
    },
    onError: (e) => toast({ kind: 'error', message: String(e) }),
  });

  const clearMutation = useMutation({
    mutationFn: () => memoryApi.clearAll(),
    onSuccess: () => {
      toast({ kind: 'success', message: 'All memories cleared.' });
      qc.invalidateQueries({ queryKey: ['memories'] });
      setClearOpen(false);
    },
    onError: (e) => toast({ kind: 'error', message: String(e) }),
  });

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-6 max-w-4xl">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Brain className="h-6 w-6 text-primary" aria-hidden="true" />
          Memory Explorer
        </h1>
        <p className="text-muted-foreground text-sm mt-1">
          Long-term memories, semantic recall, tool reliability, and execution plans
        </p>
      </div>

      {/* ── Section 1: Semantic Recall ──────────────────────────────────────── */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-5 py-3 border-b border-border bg-muted/20">
          <h2 className="text-sm font-semibold flex items-center gap-2">
            <Search className="h-4 w-4 text-primary" aria-hidden="true" />
            Semantic Recall
          </h2>
        </div>
        <div className="p-4 space-y-3">
          <form
            className="flex gap-2"
            onSubmit={(e) => { e.preventDefault(); if (recallQuery.trim()) recallMutation.mutate(); }}
          >
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" aria-hidden="true" />
              <input
                value={recallQuery}
                onChange={(e) => setRecallQuery(e.target.value)}
                placeholder="Recall memories relevant to…"
                aria-label="Recall query"
                className="w-full pl-9 pr-3 py-2 border border-input rounded-lg text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <button
              type="submit"
              disabled={recallMutation.isPending || !recallQuery.trim()}
              className="flex items-center gap-1.5 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium disabled:opacity-50"
            >
              {recallMutation.isPending
                ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                : <Search className="h-4 w-4" aria-hidden="true" />}
              Recall
            </button>
            {recalled !== null && (
              <button
                type="button"
                onClick={() => { setRecalled(null); setRecallQuery(''); }}
                className="p-2 rounded-lg hover:bg-muted/60 text-muted-foreground"
                aria-label="Clear results"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            )}
          </form>

          {recalled !== null && (
            <div className="space-y-2">
              {recalled.length === 0 ? (
                <p className="text-sm text-muted-foreground italic px-1">No relevant memories found.</p>
              ) : (
                recalled.map((r, i) => (
                  <div key={i} className="border border-border rounded-lg p-3 space-y-1.5 bg-background">
                    <p className="text-sm leading-relaxed">{r.content}</p>
                    <div className="flex items-center gap-3 flex-wrap">
                      <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium capitalize ${typeColor(r.memory_type)}`}>
                        {r.memory_type.replace('_', ' ')}
                      </span>
                      <ConfidenceBar value={r.confidence} />
                      {r.source && (
                        <span className="text-[10px] text-muted-foreground font-mono truncate max-w-[140px]" title={r.source}>
                          source: {r.source.slice(0, 12)}…
                        </span>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      </div>

      {/* ── Section 2: Long-term Memories ──────────────────────────────────── */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-5 py-3 border-b border-border flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold flex items-center gap-2">
              <Brain className="h-4 w-4 text-primary" aria-hidden="true" />
              Long-term Memories
            </h2>
            {memories.length > 0 && (
              <span className="text-[10px] bg-muted text-muted-foreground px-2 py-0.5 rounded-full">
                {memories.length}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            {/* Type filter pills */}
            <div className="flex gap-1 flex-wrap">
              <button
                onClick={() => setTypeFilter(null)}
                className={`px-2 py-0.5 text-[10px] rounded-full border transition-colors ${!typeFilter ? 'bg-primary text-primary-foreground border-primary' : 'border-border hover:bg-muted/50 text-muted-foreground'}`}
              >
                All
              </button>
              {MEMORY_TYPES.map((t) => (
                <button
                  key={t}
                  onClick={() => setTypeFilter(t === typeFilter ? null : t)}
                  className={`px-2 py-0.5 text-[10px] rounded-full border transition-colors capitalize ${t === typeFilter ? 'bg-primary text-primary-foreground border-primary' : 'border-border hover:bg-muted/50 text-muted-foreground'}`}
                >
                  {t.replace('_', ' ')}
                </button>
              ))}
            </div>
            <div className="flex gap-1.5">
              <button
                onClick={() => setAddOpen(true)}
                className="flex items-center gap-1 px-2.5 py-1 bg-primary text-primary-foreground text-xs font-medium rounded-lg hover:opacity-90"
                aria-label="Add memory"
              >
                <Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add
              </button>
              {memories.length > 0 && (
                <button
                  onClick={() => setClearOpen(true)}
                  className="flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-lg border border-destructive text-destructive hover:bg-destructive/10 transition-colors"
                  aria-label="Clear all memories"
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" /> Clear all
                </button>
              )}
            </div>
          </div>
        </div>

        {isLoading ? (
          <div className="p-5 space-y-3">
            {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-14" />)}
          </div>
        ) : memories.length === 0 ? (
          <EmptyState
            title="No memories yet"
            description={typeFilter ? `No ${typeFilter.replace('_', ' ')} memories.` : 'Memories accumulate as agents complete goals.'}
          />
        ) : (
          <ul className="divide-y divide-border">
            {(memories as MemoryEntry[]).map((m) => (
              <li key={m.id} className="px-5 py-3.5 flex items-start justify-between gap-3 hover:bg-muted/20 transition-colors">
                <div className="min-w-0 flex-1 space-y-1.5">
                  <p className="text-sm leading-relaxed">{m.content}</p>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium capitalize ${typeColor(m.memory_type)}`}>
                      {m.memory_type.replace('_', ' ')}
                    </span>
                    <ConfidenceBar value={m.confidence} />
                    {(m.tags ?? []).map((tag) => (
                      <span key={tag} className="text-[10px] bg-muted/60 text-muted-foreground px-1.5 py-0.5 rounded">
                        #{tag}
                      </span>
                    ))}
                    {m.created_at && (
                      <span className="text-[10px] text-muted-foreground">{formatDate(m.created_at)}</span>
                    )}
                  </div>
                </div>
                <button
                  aria-label="Delete memory"
                  onClick={() => deleteMutation.mutate(m.id)}
                  disabled={deleteMutation.isPending}
                  className="p-1.5 rounded-lg text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors disabled:opacity-50 shrink-0"
                >
                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* ── Section 3: Tool Reliability ─────────────────────────────────────── */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-5 py-3 border-b border-border">
          <h2 className="text-sm font-semibold flex items-center gap-2">
            <Wrench className="h-4 w-4 text-primary" aria-hidden="true" />
            Tool Reliability
            <span className="text-[10px] text-muted-foreground font-normal">(tools below 70% success threshold)</span>
          </h2>
        </div>
        {reliability.length === 0 ? (
          <div className="px-5 py-6 flex items-center gap-3 text-sm text-green-600 dark:text-green-400">
            <CheckCircle2 className="h-5 w-5 shrink-0" aria-hidden="true" />
            All tools are performing above the reliability threshold.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" aria-label="Tool reliability table">
              <thead>
                <tr className="text-left text-xs text-muted-foreground border-b border-border bg-muted/20">
                  <th className="px-5 py-2 font-medium">Tool</th>
                  <th className="px-5 py-2 font-medium">Calls</th>
                  <th className="px-5 py-2 font-medium">Failures</th>
                  <th className="px-5 py-2 font-medium">Success rate</th>
                </tr>
              </thead>
              <tbody>
                {reliability.map((t) => {
                  const pct = Math.round((t.success_rate ?? 0) * 100);
                  const rowColor = pct >= 70 ? '' : pct >= 50 ? 'bg-amber-50/40 dark:bg-amber-900/10' : 'bg-red-50/40 dark:bg-red-900/10';
                  const rateColor = pct >= 70 ? 'text-green-600 dark:text-green-400' : pct >= 50 ? 'text-amber-600 dark:text-amber-400' : 'text-red-600 dark:text-red-400';
                  const barColor = pct >= 70 ? 'bg-green-500' : pct >= 50 ? 'bg-amber-500' : 'bg-red-500';
                  return (
                    <tr key={t.tool_name} className={`border-b border-border last:border-0 ${rowColor}`}>
                      <td className="px-5 py-3 font-mono text-xs font-medium">{t.tool_name}</td>
                      <td className="px-5 py-3 text-muted-foreground">{t.total_calls ?? (t.success_count + t.failure_count)}</td>
                      <td className="px-5 py-3">
                        {t.failure_count > 0 && (
                          <span className="flex items-center gap-1 text-red-600 dark:text-red-400">
                            <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
                            {t.failure_count}
                          </span>
                        )}
                        {t.failure_count === 0 && <span className="text-muted-foreground">0</span>}
                      </td>
                      <td className="px-5 py-3">
                        <div className="flex items-center gap-2">
                          <div className="w-20 h-1.5 bg-muted rounded-full overflow-hidden">
                            <div className={`h-full rounded-full ${barColor}`} style={{ width: `${pct}%` }} />
                          </div>
                          <span className={`text-xs font-medium tabular-nums ${rateColor}`}>{pct}%</span>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Section 4: Execution Memory ─────────────────────────────────────── */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <button
          onClick={() => setExecOpen((v) => !v)}
          className="w-full flex items-center justify-between px-5 py-3 hover:bg-muted/30 transition-colors"
          aria-expanded={execOpen}
          aria-controls="exec-memory-panel"
        >
          <h2 className="text-sm font-semibold flex items-center gap-2">
            <Cpu className="h-4 w-4 text-primary" aria-hidden="true" />
            Execution Memory
            <span className="text-[10px] text-muted-foreground font-normal">(recent winning plans)</span>
          </h2>
          <ChevronDown
            className={`h-4 w-4 text-muted-foreground transition-transform ${execOpen ? 'rotate-180' : ''}`}
            aria-hidden="true"
          />
        </button>

        {execOpen && (
          <div id="exec-memory-panel">
            {execLoading ? (
              <div className="p-5 space-y-2">
                {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-10" />)}
              </div>
            ) : execMemories.length === 0 ? (
              <EmptyState
                title="No execution memories"
                description="Winning execution plans are recorded as agents complete goals."
              />
            ) : (
              <ul className="divide-y divide-border">
                {execMemories.map((m, i) => (
                  <li key={i} className="flex items-start gap-3 px-5 py-3">
                    <span className={`mt-0.5 w-2 h-2 rounded-full shrink-0 ${m.success ? 'bg-green-500' : 'bg-red-400'}`} aria-hidden="true" />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm truncate">{m.goal_text}</p>
                      {m.recorded_at && (
                        <p className="text-[10px] text-muted-foreground">{formatDate(m.recorded_at)}</p>
                      )}
                    </div>
                    <span className={`shrink-0 text-[10px] px-2 py-0.5 rounded-full ${m.success ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300' : 'bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400'}`}>
                      {m.success ? 'success' : 'failed'}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      {/* Modals */}
      {addOpen && (
        <AddMemoryModal
          onClose={() => setAddOpen(false)}
          onCreated={() => qc.invalidateQueries({ queryKey: ['memories'] })}
        />
      )}

      <ConfirmModal
        open={clearOpen}
        title="Clear all memories?"
        description="This permanently deletes all long-term memories for this tenant. This cannot be undone."
        confirmLabel="Clear all"
        variant="danger"
        isLoading={clearMutation.isPending}
        onConfirm={() => clearMutation.mutate()}
        onCancel={() => setClearOpen(false)}
      />
    </div>
  );
}
