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
  AlertTriangle, CheckCircle2, ChevronDown, Pencil, Inbox,
} from 'lucide-react';
import { memoryApi, type RecallResult, type MemoryEntry } from '@/lib/api/client';
import { toast } from '@/stores/toast';
import { Skeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { ConfirmModal } from '@/components/ui/ConfirmModal';
import { Pagination } from '@/components/ui/Pagination';
import { MissionControlLayout } from '@/components/ui/MissionControlLayout';

import { JARVISPageShell, JARVISStagger, JARVISStaggerItem } from '@/components/ui/JARVISPageShell';
// ── Constants ────────────────────────────────────────────────────────────────

const MEMORY_TYPES = ['fact', 'skill', 'preference', 'tool_usage', 'goal_completion', 'observation'];

const TYPE_COLORS: Record<string, string> = {
  fact:             'bg-telemetry-cyan/15 text-telemetry-cyan border border-telemetry-cyan/30',
  skill:            'bg-neural-violet/20 text-neural-violet border border-neural-violet/40',
  preference:       'bg-pink-500/15 text-pink-400 border border-pink-500/30',
  tool_usage:       'bg-risk-amber/15 text-risk-amber border border-risk-amber/30',
  goal_completion:  'bg-verified-green/15 text-verified-green border border-verified-green/30',
  observation:      'bg-[#0F1826]/8 text-white/50 border border-white/15',
};

function typeColor(t: string) {
  return TYPE_COLORS[t] ?? 'bg-[#0F1826]/8 text-white/50 border border-white/15';
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
  const color = pct >= 80 ? 'bg-verified-green' : pct >= 50 ? 'bg-risk-amber' : 'bg-mission-red';
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1.5 bg-[#0F1826]/10 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] text-white/40 tabular-nums">{pct}%</span>
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
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />
      <div className="relative bg-panel-graphite border border-neural-violet/30 rounded-xl shadow-2xl shadow-neural-violet/10 max-w-md w-full p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold flex items-center gap-2 text-white">
            <Brain className="h-4 w-4 text-neural-violet" aria-hidden="true" /> Add Memory
          </h2>
          <button onClick={onClose} className="text-white/40 hover:text-white/80 transition-colors" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium mb-1 text-white/60" htmlFor="mem-content">
              Content <span className="text-mission-red">*</span>
            </label>
            <textarea
              id="mem-content"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={3}
              placeholder="What should the agent remember?"
              className="w-full px-3 py-2 text-sm border border-neural-violet/20 rounded-lg bg-command-black text-white placeholder-white/25 focus:outline-none focus:ring-2 focus:ring-neural-violet/40 focus:border-neural-violet/40 resize-none transition-colors"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium mb-1 text-white/60" htmlFor="mem-type">Type</label>
              <select
                id="mem-type"
                value={memType}
                onChange={(e) => setMemType(e.target.value)}
                className="w-full px-3 py-2 text-sm border border-neural-violet/20 rounded-lg bg-command-black text-white focus:outline-none focus:ring-2 focus:ring-neural-violet/40"
              >
                {MEMORY_TYPES.map((t) => (
                  <option key={t} value={t} className="capitalize bg-command-black">{t.replace('_', ' ')}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium mb-1 text-white/60" htmlFor="mem-conf">
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
                className="w-full accent-neural-violet"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium mb-1 text-white/60" htmlFor="mem-tags">
              Tags <span className="text-white/30 font-normal">(comma-separated)</span>
            </label>
            <input
              id="mem-tags"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="ops, api, deployment"
              className="w-full px-3 py-2 text-sm border border-neural-violet/20 rounded-lg bg-command-black text-white placeholder-white/25 focus:outline-none focus:ring-2 focus:ring-neural-violet/40 transition-colors"
            />
          </div>
        </div>

        <div className="flex gap-3">
          <button
            onClick={() => mutation.mutate()}
            disabled={!content.trim() || mutation.isPending}
            className="flex-1 py-2.5 bg-neural-violet text-white text-sm font-medium rounded-lg hover:bg-neural-violet/90 disabled:opacity-50 shadow-lg shadow-neural-violet/20 transition-opacity"
          >
            {mutation.isPending ? 'Creating…' : 'Create Memory'}
          </button>
          <button onClick={onClose} className="px-4 py-2.5 border border-neural-violet/20 text-sm text-white/50 rounded-lg hover:text-white/80 hover:border-neural-violet/40 transition-colors">
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Edit Memory Modal ─────────────────────────────────────────────────────────

function EditMemoryModal({ memory, onClose, onUpdated }: { memory: MemoryEntry; onClose: () => void; onUpdated: () => void }) {
  const [content, setContent] = useState(memory.content);
  const [memType, setMemType] = useState(memory.memory_type);
  const [confidence, setConfidence] = useState(Math.round(memory.confidence * 100));
  const [tags, setTags] = useState((memory.tags ?? []).join(', '));

  const mutation = useMutation({
    mutationFn: () =>
      memoryApi.update(memory.id, {
        content,
        memory_type: memType,
        confidence: confidence / 100,
        tags: tags ? tags.split(',').map((t) => t.trim()).filter(Boolean) : [],
      }),
    onSuccess: () => {
      toast({ kind: 'success', message: 'Memory updated.' });
      onUpdated();
      onClose();
    },
    onError: (e) => toast({ kind: 'error', message: String(e) }),
  });

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />
      <div className="relative bg-panel-graphite border border-neural-violet/30 rounded-xl shadow-2xl shadow-neural-violet/10 max-w-md w-full p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold flex items-center gap-2 text-white">
            <Pencil className="h-4 w-4 text-neural-violet" aria-hidden="true" /> Edit Memory
          </h2>
          <button onClick={onClose} className="text-white/40 hover:text-white/80 transition-colors" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium mb-1 text-white/60" htmlFor="edit-mem-content">Content</label>
            <textarea
              id="edit-mem-content"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={3}
              className="w-full px-3 py-2 text-sm border border-neural-violet/20 rounded-lg bg-command-black text-white focus:outline-none focus:ring-2 focus:ring-neural-violet/40 resize-none transition-colors"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium mb-1 text-white/60" htmlFor="edit-mem-type">Type</label>
              <select
                id="edit-mem-type"
                value={memType}
                onChange={(e) => setMemType(e.target.value)}
                className="w-full px-3 py-2 text-sm border border-neural-violet/20 rounded-lg bg-command-black text-white focus:outline-none focus:ring-2 focus:ring-neural-violet/40"
              >
                {MEMORY_TYPES.map((t) => (
                  <option key={t} value={t} className="capitalize bg-command-black">{t.replace('_', ' ')}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium mb-1 text-white/60" htmlFor="edit-mem-conf">
                Confidence: {confidence}%
              </label>
              <input
                id="edit-mem-conf"
                type="range"
                min={10}
                max={100}
                step={5}
                value={confidence}
                onChange={(e) => setConfidence(Number(e.target.value))}
                className="w-full accent-neural-violet"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium mb-1 text-white/60" htmlFor="edit-mem-tags">
              Tags <span className="text-white/30 font-normal">(comma-separated)</span>
            </label>
            <input
              id="edit-mem-tags"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-neural-violet/20 rounded-lg bg-command-black text-white focus:outline-none focus:ring-2 focus:ring-neural-violet/40 transition-colors"
            />
          </div>
        </div>
        <div className="flex gap-3">
          <button
            onClick={() => mutation.mutate()}
            disabled={!content.trim() || mutation.isPending}
            className="flex-1 py-2.5 bg-neural-violet text-white text-sm font-medium rounded-lg hover:bg-neural-violet/90 disabled:opacity-50 shadow-lg shadow-neural-violet/20 transition-opacity"
          >
            {mutation.isPending ? 'Saving…' : 'Save Changes'}
          </button>
          <button onClick={onClose} className="px-4 py-2.5 border border-neural-violet/20 text-sm text-white/50 rounded-lg hover:text-white/80 hover:border-neural-violet/40 transition-colors">
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
  const [editingMemory, setEditingMemory] = useState<MemoryEntry | null>(null);
  const [deleteMemoryId, setDeleteMemoryId] = useState<string | null>(null);
  const PAGE_SIZE = 20;
  const [page, setPage] = useState(1);

  // ── Queries ────────────────────────────────────────────────────────────────

  const { data: memoryData, isLoading } = useQuery({
    queryKey: ['memories', typeFilter, page],
    queryFn: () => memoryApi.list({
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
      memoryType: typeFilter ?? undefined,
    }),
  });
  // Support both paginated `{ items, total }` and legacy flat array responses
  const memories = (memoryData as any)?.items ?? (Array.isArray(memoryData) ? memoryData : []);
  const total: number = (memoryData as any)?.total ?? (memories as MemoryEntry[]).length;
  const safeMemories: MemoryEntry[] = Array.isArray(memories) ? (memories as MemoryEntry[]) : [];

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
      void qc.invalidateQueries({ queryKey: ['memories'] });
      setDeleteMemoryId(null);
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
    <JARVISPageShell>

      {/* Accessibility: announce loading state */}
      <div aria-live="polite" aria-atomic="true" className="sr-only">{isLoading ? "Loading…" : ""}</div>
    <MissionControlLayout>
      <div className="space-y-6 max-w-4xl">
        {/* Page header */}
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-lg bg-neural-violet/20 border border-neural-violet/30 shadow-lg shadow-neural-violet/10">
            <Brain className="h-5 w-5 text-neural-violet" aria-hidden="true" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight">Memory Explorer</h1>
            <p className="text-white/40 text-sm mt-0.5">
              Long-term memories, semantic recall, tool reliability, and execution plans
            </p>
          </div>
        </div>

        {/* ── Section 1: Semantic Recall ──────────────────────────────────────── */}
        <div className="bg-panel-graphite border border-neural-violet/20 rounded-xl overflow-hidden">
          <div className="px-5 py-3 border-b border-neural-violet/15 bg-command-black/40">
            <h2 className="text-sm font-semibold flex items-center gap-2 text-white/80">
              <Search className="h-4 w-4 text-telemetry-cyan" aria-hidden="true" />
              Semantic Recall
              <span className="text-[10px] text-white/30 font-normal ml-1">vector similarity search</span>
            </h2>
          </div>
          <div className="p-4 space-y-3">
            <form
              className="flex gap-2"
              onSubmit={(e) => { e.preventDefault(); if (recallQuery.trim()) recallMutation.mutate(); }}
            >
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-white/30 pointer-events-none" aria-hidden="true" />
                <input
                  value={recallQuery}
                  onChange={(e) => setRecallQuery(e.target.value)}
                  placeholder="Recall memories relevant to…"
                  aria-label="Recall query"
                  className="w-full pl-9 pr-3 py-2 border border-neural-violet/20 rounded-lg text-sm bg-command-black text-white placeholder-white/25 focus:outline-none focus:ring-2 focus:ring-neural-violet/40 focus:border-neural-violet/40 transition-colors"
                />
              </div>
              <button
                type="submit"
                disabled={recallMutation.isPending || !recallQuery.trim()}
                className="flex items-center gap-1.5 px-4 py-2 bg-neural-violet text-white rounded-lg text-sm font-medium disabled:opacity-50 hover:bg-neural-violet/90 shadow-lg shadow-neural-violet/20 transition-opacity"
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
                  className="p-2 rounded-lg hover:bg-white/5 text-white/40 hover:text-white/70 transition-colors"
                  aria-label="Clear results"
                >
                  <X className="h-4 w-4" aria-hidden="true" />
                </button>
              )}
            </form>

            {recalled !== null && (
              <div className="space-y-2">
                {recalled.length === 0 ? (
                  <p className="text-sm text-white/40 italic px-1">No relevant memories found.</p>
                ) : (
                  recalled.map((r, i) => (
                    <div key={i} className="border border-neural-violet/20 rounded-lg p-3 space-y-1.5 bg-command-black/60">
                      <p className="text-sm text-white/80 leading-relaxed">{r.content}</p>
                      <div className="flex items-center gap-3 flex-wrap">
                        <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium capitalize ${typeColor(r.memory_type)}`}>
                          {r.memory_type.replace('_', ' ')}
                        </span>
                        <ConfidenceBar value={r.confidence} />
                        {r.source && (
                          <span className="text-[10px] text-white/30 font-mono truncate max-w-[140px]" title={r.source}>
                            src: {r.source.slice(0, 12)}…
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
        <div className="bg-panel-graphite border border-neural-violet/20 rounded-xl overflow-hidden">
          <div className="px-5 py-3 border-b border-neural-violet/15 bg-command-black/40 flex items-center justify-between gap-3 flex-wrap">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold flex items-center gap-2 text-white/80">
                <Brain className="h-4 w-4 text-neural-violet" aria-hidden="true" />
                Long-term Memories
              </h2>
              {safeMemories.length > 0 && (
                <span className="text-[10px] bg-neural-violet/20 text-neural-violet px-2 py-0.5 rounded-full border border-neural-violet/30 font-mono">
                  {total}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              {/* Type filter pills */}
              <div className="flex gap-1 flex-wrap">
                <button
                  onClick={() => { setTypeFilter(null); setPage(1); }}
                  className={`px-2 py-0.5 text-[10px] rounded-full border transition-colors ${!typeFilter ? 'bg-neural-violet text-white border-neural-violet' : 'border-neural-violet/20 hover:border-neural-violet/40 text-white/40 hover:text-white/70'}`}
                >
                  All
                </button>
                {MEMORY_TYPES.map((t) => (
                  <button
                    key={t}
                    onClick={() => { setTypeFilter(t === typeFilter ? null : t); setPage(1); }}
                    className={`px-2 py-0.5 text-[10px] rounded-full border transition-colors capitalize ${t === typeFilter ? 'bg-neural-violet text-white border-neural-violet' : 'border-neural-violet/20 hover:border-neural-violet/40 text-white/40 hover:text-white/70'}`}
                  >
                    {t.replace('_', ' ')}
                  </button>
                ))}
              </div>
              <div className="flex gap-1.5">
                <button
                  onClick={() => setAddOpen(true)}
                  className="flex items-center gap-1 px-2.5 py-1 bg-neural-violet text-white text-xs font-medium rounded-lg hover:bg-neural-violet/90 shadow-sm shadow-neural-violet/20 transition-opacity"
                  aria-label="Add memory"
                >
                  <Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add
                </button>
                {safeMemories.length > 0 && (
                  <button
                    onClick={() => setClearOpen(true)}
                    className="flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-lg border border-mission-red/30 text-mission-red/70 hover:bg-mission-red/10 hover:text-mission-red transition-colors"
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
          ) : safeMemories.length === 0 ? (
            <EmptyState
              
          icon={<Inbox size={40} />}
          title="No memories yet"
              description={typeFilter ? `No ${typeFilter.replace('_', ' ')} memories.` : 'Memories accumulate as agents complete goals.'}          variant="float"
        
            />
          ) : (
            <>
            <JARVISStagger className="divide-y divide-neural-violet/10">
              {safeMemories.map((m) => (
                <JARVISStaggerItem key={m.id} interactive className="px-5 py-3.5 flex items-start justify-between gap-3 hover:bg-neural-violet/5 transition-colors">
                  <div className="min-w-0 flex-1 space-y-1.5">
                    <p className="text-sm text-white/80 leading-relaxed">{m.content}</p>
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium capitalize ${typeColor(m.memory_type)}`}>
                        {m.memory_type.replace('_', ' ')}
                      </span>
                      <ConfidenceBar value={m.confidence} />
                      {(m.tags ?? []).map((tag) => (
                        <span key={tag} className="text-[10px] bg-neural-violet/10 text-neural-violet/70 px-1.5 py-0.5 rounded border border-neural-violet/20">
                          #{tag}
                        </span>
                      ))}
                      {m.created_at && (
                        <span className="text-[10px] text-white/30 font-mono">{formatDate(m.created_at)}</span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      aria-label="Edit memory"
                      onClick={() => setEditingMemory(m)}
                      className="p-1.5 rounded-lg text-white/30 hover:text-neural-violet hover:bg-neural-violet/10 transition-colors"
                    >
                      <Pencil className="h-4 w-4" aria-hidden="true" />
                    </button>
                    <button
                      aria-label="Delete memory"
                      onClick={() => setDeleteMemoryId(m.id)}
                      disabled={deleteMutation.isPending}
                      className="p-1.5 rounded-lg text-white/30 hover:text-mission-red hover:bg-mission-red/10 transition-colors disabled:opacity-50"
                    >
                      <Trash2 className="h-4 w-4" aria-hidden="true" />
                    </button>
                  </div>
                </JARVISStaggerItem>
              ))}
            </JARVISStagger>
            {total > PAGE_SIZE && (
              <div className="px-5 py-3 border-t border-neural-violet/15">
                <Pagination
                  page={page}
                  pageSize={PAGE_SIZE}
                  total={total}
                  onPageChange={setPage}
                />
              </div>
            )}
            </>
          )}
        </div>

        {/* ── Section 3: Tool Reliability ─────────────────────────────────────── */}
        <div className="bg-panel-graphite border border-neural-violet/20 rounded-xl overflow-hidden">
          <div className="px-5 py-3 border-b border-neural-violet/15 bg-command-black/40">
            <h2 className="text-sm font-semibold flex items-center gap-2 text-white/80">
              <Wrench className="h-4 w-4 text-risk-amber" aria-hidden="true" />
              Tool Reliability
              <span className="text-[10px] text-white/30 font-normal">(tools below 70% success threshold)</span>
            </h2>
          </div>
          {reliability.length === 0 ? (
            <div className="px-5 py-6 flex items-center gap-3 text-sm text-verified-green">
              <CheckCircle2 className="h-5 w-5 shrink-0" aria-hidden="true" />
              All tools are performing above the reliability threshold.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm" aria-label="Tool reliability table">
                <thead>
                  <tr className="text-left text-xs text-white/30 border-b border-neural-violet/15 bg-command-black/30">
                    <th className="px-5 py-2 font-medium uppercase tracking-wider">Tool</th>
                    <th className="px-5 py-2 font-medium uppercase tracking-wider">Calls</th>
                    <th className="px-5 py-2 font-medium uppercase tracking-wider">Failures</th>
                    <th className="px-5 py-2 font-medium uppercase tracking-wider">Success rate</th>
                  </tr>
                </thead>
                <tbody>
                  {reliability.map((t) => {
                    const pct = Math.round((t.success_rate ?? 0) * 100);
                    const rowColor = pct >= 70 ? '' : pct >= 50 ? 'bg-risk-amber/5' : 'bg-mission-red/5';
                    const rateColor = pct >= 70 ? 'text-verified-green' : pct >= 50 ? 'text-risk-amber' : 'text-mission-red';
                    const barColor = pct >= 70 ? 'bg-verified-green' : pct >= 50 ? 'bg-risk-amber' : 'bg-mission-red';
                    return (
                      <tr key={t.tool_name} className={`border-b border-neural-violet/10 last:border-0 ${rowColor}`}>
                        <td className="px-5 py-3 font-mono text-xs font-medium text-telemetry-cyan">{t.tool_name}</td>
                        <td className="px-5 py-3 text-white/40 font-mono text-xs">{t.total_calls ?? (t.success_count + t.failure_count)}</td>
                        <td className="px-5 py-3">
                          {t.failure_count > 0 && (
                            <span className="flex items-center gap-1 text-mission-red text-xs">
                              <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
                              {t.failure_count}
                            </span>
                          )}
                          {t.failure_count === 0 && <span className="text-white/30 text-xs">0</span>}
                        </td>
                        <td className="px-5 py-3">
                          <div className="flex items-center gap-2">
                            <div className="w-20 h-1.5 bg-[#0F1826]/10 rounded-full overflow-hidden">
                              <div className={`h-full rounded-full ${barColor}`} style={{ width: `${pct}%` }} />
                            </div>
                            <span className={`text-xs font-medium tabular-nums font-mono ${rateColor}`}>{pct}%</span>
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
        <div className="bg-panel-graphite border border-neural-violet/20 rounded-xl overflow-hidden">
          <button
            onClick={() => setExecOpen((v) => !v)}
            className="w-full flex items-center justify-between px-5 py-3 hover:bg-neural-violet/5 transition-colors"
            aria-expanded={execOpen}
            aria-controls="exec-memory-panel"
          >
            <h2 className="text-sm font-semibold flex items-center gap-2 text-white/80">
              <Cpu className="h-4 w-4 text-telemetry-cyan" aria-hidden="true" />
              Execution Memory
              <span className="text-[10px] text-white/30 font-normal">(recent winning plans)</span>
            </h2>
            <ChevronDown
              className={`h-4 w-4 text-white/30 transition-transform ${execOpen ? 'rotate-180' : ''}`}
              aria-hidden="true"
            />
          </button>

          {execOpen && (
            <div id="exec-memory-panel" className="border-t border-neural-violet/15">
              {execLoading ? (
                <div className="p-5 space-y-2">
                  {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-10" />)}
                </div>
              ) : execMemories.length === 0 ? (
                <EmptyState
          icon={<Inbox size={40} />}
          title="No execution memories"
          description="Winning execution plans are recorded as agents complete goals."
          variant="float"
        />
              ) : (
                <ul className="divide-y divide-neural-violet/10">
                  {execMemories.map((m, i) => (
                    <li key={`exec-${m.goal_text?.slice(0, 20) ?? ''}-${i}`} className="flex items-start gap-3 px-5 py-3 hover:bg-neural-violet/5 transition-colors">
                      <span className={`mt-0.5 w-2 h-2 rounded-full shrink-0 ${m.success ? 'bg-verified-green' : 'bg-mission-red'}`} aria-hidden="true" />
                      <div className="min-w-0 flex-1">
                        <p className="text-sm text-white/70 truncate">{m.goal_text}</p>
                        {m.recorded_at && (
                          <p className="text-[10px] text-white/30 font-mono">{formatDate(m.recorded_at)}</p>
                        )}
                      </div>
                      <span className={`shrink-0 text-[10px] px-2 py-0.5 rounded-full border font-medium ${
                        m.success
                          ? 'bg-verified-green/15 text-verified-green border-verified-green/30'
                          : 'bg-mission-red/15 text-mission-red border-mission-red/30'
                      }`}>
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
            onCreated={() => void qc.invalidateQueries({ queryKey: ['memories'] })}
          />
        )}

        {editingMemory && (
          <EditMemoryModal
            memory={editingMemory}
            onClose={() => setEditingMemory(null)}
            onUpdated={() => void qc.invalidateQueries({ queryKey: ['memories'] })}
          />
        )}

        <ConfirmModal
          open={!!deleteMemoryId}
          title="Delete memory?"
          description="This memory entry will be permanently removed."
          confirmLabel="Delete"
          variant="danger"
          isLoading={deleteMutation.isPending}
          onConfirm={() => { if (deleteMemoryId) deleteMutation.mutate(deleteMemoryId); }}
          onCancel={() => setDeleteMemoryId(null)}
        />

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
    </MissionControlLayout>
    </JARVISPageShell>
  );
}
