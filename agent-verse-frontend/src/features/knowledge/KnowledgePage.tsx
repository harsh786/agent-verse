/**
 * KnowledgePage — World-Class AI Knowledge Management
 *
 * 5 tabs:
 *   Collections  — card grid with health gauges
 *   Ask AI       — RAG chat with streaming citations (WOW feature)
 *   Ingest       — source type pills + drag-and-drop + progress
 *   Search       — advanced search with highlighted results
 *   Analytics    — collection health, cache stats, source distribution
 */
import { useCallback, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  BookOpen, Brain, BarChart2, CheckCircle, ChevronRight, ClipboardCopy,
  Database, ExternalLink, Eye, FileText, Globe, Link, Loader2, MessageSquare, Plus,
  RefreshCw, Search, Sparkles, Trash2, Upload, X, Zap, XCircle,
} from 'lucide-react';
import { toast } from '@/stores/toast';
import { ConfirmModal } from '@/components/ui/ConfirmModal';
import { Skeleton } from '@/components/ui/Skeleton';
import { Pagination } from '@/components/ui/Pagination';
import { ApiError, apiFetch, llmErrorMessage } from '@/lib/api/client';

import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';
// ── Types ──────────────────────────────────────────────────────────────────────

interface Collection { collection_id: string; name: string; doc_count?: number; embedder?: string; created_at?: string; }
interface SearchResult { doc_id?: string; chunk_id?: string; content: string; score: number; source_url?: string; metadata?: Record<string, unknown>; }
interface CollectionStats {
  collection_id: string; name: string; doc_count: number; chunk_count: number;
  embedding_coverage_pct: number; avg_chunk_length: number;
  source_type_distribution: Record<string, number>; embedder: string; health_score: number;
}
interface Citation { index: number; chunk_id: string; collection_id: string; score: number; source_url: string; page_number: number | null; excerpt: string; }
interface RagAnswer { answer: string; citations: Citation[]; collections_searched: number; chunks_retrieved: number; question: string; }
type Tab = 'collections' | 'ask' | 'ingest' | 'search' | 'analytics' | 'documents';

function HealthGauge({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color = pct >= 80 ? '#22c55e' : pct >= 50 ? '#f59e0b' : '#ef4444';
  const r = 24, circ = 2 * Math.PI * r;
  return (
    <div className="flex flex-col items-center">
      <svg width="64" height="64" viewBox="0 0 64 64">
        <circle cx="32" cy="32" r={r} fill="none" stroke="#e5e7eb" strokeWidth="6" />
        <circle cx="32" cy="32" r={r} fill="none" stroke={color} strokeWidth="6" strokeLinecap="round"
          strokeDasharray={`${circ * (pct / 100)} ${circ * (1 - pct / 100)}`}
          transform="rotate(-90 32 32)" style={{ transition: 'stroke-dasharray 0.5s ease' }} />
        <text x="32" y="36" textAnchor="middle" fontSize="12" fontWeight="bold" fill={color}>{pct}%</text>
      </svg>
      <span className="text-[10px] text-muted-foreground">Health</span>
    </div>
  );
}

// ── Re-embed (KB-25) ──────────────────────────────────────────────────────────

interface ReembedProgress {
  status: 'never_run' | 'queued' | 'running' | 'completed' | 'failed';
  job_id?: string; processed?: number; total?: number | null; dimension?: number; error?: string;
}

function reembedErrorMessage(e: unknown): string {
  if (e instanceof ApiError && e.status === 409) return 'A re-embed of this collection is already running.';
  if (e instanceof ApiError && e.status === 403) return 'Only admins can re-embed a collection.';
  if (e instanceof ApiError) return `Re-embed could not be started (${e.status}): ${e.message}`;
  return 'Re-embed could not be started.';
}

/** Re-embed a collection with the deployment's current embedder, with job progress. */
function ReembedControl({ collectionId }: { collectionId: string }) {
  const qc = useQueryClient();
  const [watching, setWatching] = useState(false);
  const { data: progress } = useQuery<ReembedProgress>({
    queryKey: ['collection-reembed', collectionId],
    queryFn: () => apiFetch(`/knowledge/collections/${collectionId}/re-embed`, undefined, { silenceServerErrorToast: true }),
    enabled: watching,
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === 'queued' || s === 'running' ? 1000 : false;
    },
  });
  const mutation = useMutation({
    mutationFn: () => apiFetch<{ job_id: string }>(`/knowledge/collections/${collectionId}/re-embed`, { method: 'POST' }),
    onSuccess: () => {
      setWatching(true);
      void qc.invalidateQueries({ queryKey: ['collection-reembed', collectionId] });
      toast({ kind: 'success', message: 'Re-embed queued.' });
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409) setWatching(true);
      toast({ kind: 'error', message: reembedErrorMessage(e) });
    },
  });
  const busy = mutation.isPending || progress?.status === 'queued' || progress?.status === 'running';
  let label: string | null = null;
  if (watching && progress) {
    if (progress.status === 'queued') label = 'Re-embed queued…';
    else if (progress.status === 'running') label = `Re-embedding… ${progress.processed ?? 0}/${progress.total ?? '?'} chunks`;
    else if (progress.status === 'completed') label = `Re-embedded ${progress.processed ?? 0} chunks${progress.dimension ? ` (${progress.dimension}-d)` : ''}`;
    else if (progress.status === 'failed') label = `Re-embed failed: ${progress.error ?? 'unknown error'}`;
  }
  return (
    <div className="flex items-center gap-2 min-w-0">
      <button data-testid={`reembed-collection-${collectionId}`} onClick={() => mutation.mutate()} disabled={busy}
        title="Re-embed every chunk with the current embedding model"
        className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1 disabled:opacity-50">
        <RefreshCw className={`h-3.5 w-3.5 ${busy ? 'animate-spin' : ''}`} /> Re-embed
      </button>
      {label && (
        <span data-testid={`reembed-status-${collectionId}`}
          className={`text-[10px] truncate ${progress?.status === 'failed' ? 'text-red-500' : 'text-muted-foreground'}`}>
          {label}
        </span>
      )}
    </div>
  );
}

// ── Collections Tab ───────────────────────────────────────────────────────────

function CollectionsTab() {
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newEmbedder, setNewEmbedder] = useState('voyage');
  const [expanded, setExpanded] = useState<string | null>(null);
  const [deleteCollectionId, setDeleteCollectionId] = useState<string | null>(null);

  const { data: collections = [], isLoading } = useQuery<Collection[]>({
    queryKey: ['knowledge-collections'],
    queryFn: () => apiFetch('/knowledge/collections'),
  });
  const { data: expandedStats } = useQuery<CollectionStats>({
    queryKey: ['collection-stats', expanded],
    queryFn: () => apiFetch(`/knowledge/collections/${expanded}/stats`),
    enabled: !!expanded,
  });

  const createMutation = useMutation({
    mutationFn: () => apiFetch('/knowledge/collections', { method: 'POST', body: JSON.stringify({ name: newName, embedder_type: newEmbedder }) }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['knowledge-collections'] }); setShowCreate(false); setNewName(''); toast({ kind: 'success', message: 'Collection created.' }); },
    onError: (e) => toast({ kind: 'error', message: String(e) }),
  });
  const deleteMutation = useMutation({
    mutationFn: (id: string) => apiFetch(`/knowledge/collections/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['knowledge-collections'] });
      setDeleteCollectionId(null);
    },
  });

  if (isLoading) return <div className="flex justify-center py-12"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <span className="text-sm text-muted-foreground">{collections.length} collection{collections.length !== 1 ? 's' : ''}</span>
        <button onClick={() => setShowCreate(true)} className="flex items-center gap-1.5 px-3 py-2 bg-primary text-primary-foreground rounded-md text-sm">
          <Plus className="h-4 w-4" /> New Collection
        </button>
      </div>
      {showCreate && (
        <div className="bg-card border border-border rounded-xl p-4 space-y-3">
          <h3 className="font-medium text-sm">Create Collection</h3>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Name *</label>
              <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="my-knowledge-base"
                className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background" />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Embedder</label>
              <select value={newEmbedder} onChange={(e) => setNewEmbedder(e.target.value)}
                className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background">
                {['voyage', 'openai', 'sentence-transformers'].map((e) => <option key={e} value={e}>{e}</option>)}
              </select>
            </div>
          </div>
          <div className="flex gap-2">
            <button onClick={() => createMutation.mutate()} disabled={!newName.trim() || createMutation.isPending}
              className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50">
              {createMutation.isPending ? 'Creating…' : 'Create'}
            </button>
            <button onClick={() => setShowCreate(false)} className="px-4 py-2 border border-border rounded-md text-sm">Cancel</button>
          </div>
        </div>
      )}
      {collections.length === 0 ? (
        <div className="flex flex-col items-center py-14 text-muted-foreground gap-2">
          <Database className="h-10 w-10 opacity-30" />
          <p className="text-sm">No collections yet — create one to start ingesting documents.</p>
        </div>
      ) : (
        <div data-testid="collections-grid" className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {collections.map((c) => (
            <div key={c.collection_id} data-testid={`collection-card-${c.collection_id}`}
              className="bg-card border border-border rounded-xl overflow-hidden">
              <div className="p-4 space-y-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <p className="font-semibold truncate">{c.name}</p>
                    <p className="text-xs text-muted-foreground font-mono mt-0.5">{c.collection_id.slice(0, 16)}…</p>
                  </div>
                  <span className="text-xs bg-violet-100 text-violet-700 px-2 py-0.5 rounded shrink-0">{c.embedder ?? 'voyage'}</span>
                </div>
                <div className="flex items-center gap-3 text-sm">
                  <span className="flex items-center gap-1 text-muted-foreground"><FileText className="h-3.5 w-3.5" /> {c.doc_count ?? 0} docs</span>
                </div>
              </div>
              {expanded === c.collection_id && expandedStats && (
                <div className="border-t border-border px-4 py-3 bg-muted/20 space-y-2">
                  <div className="flex items-center gap-3">
                    <HealthGauge score={expandedStats.health_score} />
                    <div className="text-xs space-y-1">
                      <p><span className="text-muted-foreground">Chunks:</span> {expandedStats.chunk_count}</p>
                      <p><span className="text-muted-foreground">Embed coverage:</span> {expandedStats.embedding_coverage_pct}%</p>
                      <p><span className="text-muted-foreground">Avg chunk:</span> {expandedStats.avg_chunk_length} chars</p>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {Object.entries(expandedStats.source_type_distribution).map(([k, v]) => (
                      <span key={k} className="text-[10px] bg-muted px-1.5 py-0.5 rounded">{k}: {v}</span>
                    ))}
                  </div>
                </div>
              )}
              <div className="border-t border-border px-4 py-2.5 flex items-center justify-between">
                <button onClick={() => setExpanded(expanded === c.collection_id ? null : c.collection_id)}
                  className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1">
                  <ChevronRight className={`h-3.5 w-3.5 transition-transform ${expanded === c.collection_id ? 'rotate-90' : ''}`} />
                  {expanded === c.collection_id ? 'Hide stats' : 'View stats'}
                </button>
                <ReembedControl collectionId={c.collection_id} />
                <button data-testid={`delete-collection-${c.collection_id}`} onClick={() => setDeleteCollectionId(c.collection_id)}
                  className="p-1.5 text-muted-foreground hover:text-red-500 rounded">
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      <ConfirmModal
        open={!!deleteCollectionId}
        title="Delete collection?"
        description="All documents and embeddings in this collection will be permanently deleted. This cannot be undone."
        confirmLabel="Delete Collection"
        variant="danger"
        isLoading={deleteMutation.isPending}
        onConfirm={() => { if (deleteCollectionId) deleteMutation.mutate(deleteCollectionId); }}
        onCancel={() => setDeleteCollectionId(null)}
      />
    </div>
  );
}

// ── Ask AI Tab ────────────────────────────────────────────────────────────────

/** Score badge shared by inline citation hover previews and the Sources list. */
function ScoreBadge({ score }: { score: number }) {
  return (
    <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${score > 0.8 ? 'bg-green-100 text-green-700' : score > 0.6 ? 'bg-amber-100 text-amber-700' : 'bg-muted text-muted-foreground'}`}>
      {(score * 100).toFixed(0)}%
    </span>
  );
}

/**
 * Renders an answer with inline `[n]` citation markers hover/focus-linked to
 * their source card below: hovering (or focusing via keyboard) a marker shows
 * a small chunk preview and highlights the matching Sources row; clicking
 * scrolls that row into view. Falls back to plain text for `[n]` sequences
 * that don't match a real citation index (never fabricates a link).
 */
function AnswerWithCitations({ answer, citations }: { answer: string; citations: Citation[] }) {
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const sourceRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const citationByIndex = new Map(citations.map((c) => [c.index, c]));

  const activate = useCallback((n: number) => setActiveIndex(n), []);
  const deactivate = useCallback(() => setActiveIndex(null), []);
  const jumpToSource = useCallback((n: number) => {
    setActiveIndex(n);
    sourceRefs.current[n]?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, []);

  const parts = answer.split(/(\[\d+\])/g);

  return (
    <>
      <div className="p-4">
        <p className="text-sm leading-relaxed whitespace-pre-wrap">
          {parts.map((part, i) => {
            const m = /^\[(\d+)\]$/.exec(part);
            const citation = m ? citationByIndex.get(Number(m[1])) : undefined;
            if (!m || !citation) return <span key={i}>{part}</span>;
            const n = citation.index;
            const tooltipId = `citation-tooltip-${n}`;
            return (
              <span key={i} className="relative inline-block">
                <button
                  type="button"
                  data-testid={`citation-marker-${n}`}
                  aria-describedby={activeIndex === n ? tooltipId : undefined}
                  aria-label={`Citation ${n}, source: ${citation.excerpt.slice(0, 60)}`}
                  onMouseEnter={() => activate(n)}
                  onMouseLeave={deactivate}
                  onFocus={() => activate(n)}
                  onBlur={deactivate}
                  onClick={() => jumpToSource(n)}
                  className="mx-0.5 px-1 py-0.5 rounded bg-violet-100 text-violet-700 font-mono text-[11px] align-baseline hover:bg-violet-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
                >
                  [{n}]
                </button>
                {activeIndex === n && (
                  <span
                    id={tooltipId}
                    role="tooltip"
                    className="absolute z-20 bottom-full left-1/2 -translate-x-1/2 mb-1 w-56 rounded-lg border border-border bg-popover text-popover-foreground text-[11px] p-2 shadow-md"
                  >
                    <span className="line-clamp-3">{citation.excerpt}</span>
                    <span className="flex items-center gap-1.5 mt-1">
                      <ScoreBadge score={citation.score} />
                    </span>
                  </span>
                )}
              </span>
            );
          })}
        </p>
      </div>
      {citations.length > 0 && (
        <div data-testid="citations-panel" className="border-t border-border px-4 py-3 bg-muted/20 space-y-2">
          <p className="text-xs font-medium text-muted-foreground">Sources</p>
          {citations.map((c) => (
            <div
              key={c.index}
              ref={(el) => { sourceRefs.current[c.index] = el; }}
              data-testid={`citation-source-${c.index}`}
              onMouseEnter={() => activate(c.index)}
              onMouseLeave={deactivate}
              className={`flex items-start gap-2 text-xs rounded-lg -mx-1.5 px-1.5 py-1 transition-colors ${activeIndex === c.index ? 'bg-violet-100/60 ring-1 ring-violet-300' : ''}`}
            >
              <span className="bg-violet-100 text-violet-700 px-1.5 py-0.5 rounded font-mono shrink-0">[{c.index}]</span>
              <div className="flex-1 min-w-0">
                <p className="text-muted-foreground line-clamp-2">{c.excerpt}</p>
                <div className="flex items-center gap-2 mt-0.5">
                  <ScoreBadge score={c.score} />
                  {c.source_url && <a href={c.source_url} target="_blank" rel="noreferrer" className="flex items-center gap-0.5 text-blue-500 hover:underline"><ExternalLink className="h-3 w-3" />source</a>}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

/** A clear message for a failed Ask, instead of the generic error toast. */
function askErrorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 429) {
      return 'Your LLM budget is exhausted, so no answer was generated. Raise the budget or try again later.';
    }
    if (e.status === 503 || e.status === 504) {
      return 'The answer model is unavailable or timed out. Try again in a moment.';
    }
    return e.message || `Ask failed (${e.status})`;
  }
  return String(e);
}

function AskAITab() {
  const [question, setQuestion] = useState('');
  const [history, setHistory] = useState<Array<{ q: string; a: RagAnswer }>>([]);
  const [selectedCollections, setSelectedCollections] = useState<string[]>([]);
  const answerRef = useRef<HTMLDivElement>(null);

  const { data: collections = [] } = useQuery<Collection[]>({
    queryKey: ['knowledge-collections'],
    queryFn: () => apiFetch('/knowledge/collections'),
  });

  const askMutation = useMutation({
    // 503/504 get a specific message below, not the generic "Server error" toast.
    mutationFn: (q: string) => apiFetch<RagAnswer>('/knowledge/chat', {
      method: 'POST',
      body: JSON.stringify({ question: q, collection_ids: selectedCollections, top_k: 5 }),
    }, { silenceServerErrorToast: true }),
    onSuccess: (r, q) => {
      setHistory((h) => [{ q, a: r }, ...h.slice(0, 4)]);
      setQuestion('');
      setTimeout(() => answerRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 100);
    },
    onError: (e) => toast({ kind: 'error', message: askErrorMessage(e) }),
  });

  // Always the real RAG endpoint (POST /knowledge/chat, scoped by
  // collection_ids). There is no /knowledge/collections/{id}/query/stream: the
  // single-collection "streaming" path always 404'd and fell back here.
  const handleAsk = () => {
    if (!question.trim()) return;
    askMutation.mutate(question);
  };

  const exampleQuestions = [
    'Summarize the main topics across all documents',
    'What are the key technical decisions made?',
    'List all action items and their owners',
    'What APIs are documented here?',
  ];

  return (
    <div className="space-y-5">
      {/* Question input */}
      <div data-testid="ask-ai-panel" className="bg-card border border-border rounded-xl p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-violet-500" />
          <h3 className="font-medium text-sm">Ask your knowledge base</h3>
          <span className="ml-auto text-xs text-muted-foreground bg-violet-50 text-violet-600 px-2 py-0.5 rounded">RAG-powered</span>
        </div>
        <textarea data-testid="ask-input" value={question} onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && e.metaKey && question.trim()) handleAsk(); }}
          rows={3} placeholder="Ask anything about your knowledge base…"
          className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background resize-none" />
        {collections.length > 0 && (
          <div>
            <p className="text-xs text-muted-foreground mb-1.5">Filter collections (empty = all):</p>
            <div className="flex flex-wrap gap-1.5">
              {collections.map((c) => (
                <button key={c.collection_id}
                  onClick={() => setSelectedCollections((prev) => prev.includes(c.collection_id) ? prev.filter((id) => id !== c.collection_id) : [...prev, c.collection_id])}
                  className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${selectedCollections.includes(c.collection_id) ? 'bg-primary text-primary-foreground border-primary' : 'border-border hover:bg-muted'}`}>
                  {c.name}
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="flex flex-wrap gap-1.5">
          <span className="text-xs text-muted-foreground self-center">Try:</span>
          {exampleQuestions.map((q) => (
            <button key={q} onClick={() => setQuestion(q)} className="text-xs px-2 py-1 bg-muted rounded hover:bg-muted/80 truncate max-w-[200px]">{q}</button>
          ))}
        </div>
        <button data-testid="ask-btn" onClick={() => handleAsk()} disabled={!question.trim() || askMutation.isPending}
          className="flex items-center gap-2 px-4 py-2 bg-violet-600 text-white rounded-md text-sm disabled:opacity-50">
          {askMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <MessageSquare className="h-4 w-4" />}
          {askMutation.isPending ? 'Searching & synthesizing…' : 'Ask (⌘+Enter)'}
        </button>
      </div>

      {/* Current answer */}
      {history.length > 0 && (
        <div ref={answerRef} data-testid="answer-panel" className="space-y-3">
          {history.map(({ q, a }, i) => (
            <div key={i} className={`border border-border rounded-xl overflow-hidden ${i > 0 ? 'opacity-60' : ''}`}>
              <div className="px-4 py-2.5 bg-muted/40 border-b border-border">
                <p className="font-medium text-sm">{q}</p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {a.chunks_retrieved} chunks from {a.collections_searched} collection{a.collections_searched !== 1 ? 's' : ''}
                </p>
              </div>
              <AnswerWithCitations answer={a.answer} citations={a.citations} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Ingest Tab ────────────────────────────────────────────────────────────────

const SOURCE_TYPES = [
  { value: 'text', label: 'Text' }, { value: 'markdown', label: 'Markdown' }, { value: 'url', label: 'URL' },
  { value: 'pdf', label: 'PDF' }, { value: 'docx', label: 'DOCX' }, { value: 'pptx', label: 'PowerPoint' },
  { value: 'image', label: 'Image' }, { value: 'git', label: 'Git' },
  { value: 'github', label: 'GitHub' }, { value: 'openapi', label: 'OpenAPI' }, { value: 'confluence', label: 'Confluence' },
  { value: 'jira', label: 'Jira' }, { value: 'slack', label: 'Slack' },
];

/** Source types that can only be ingested by uploading a file. */
const FILE_ONLY_SOURCES = ['pdf', 'docx', 'pptx', 'image'];
/** Source types whose content is typed/pasted into the textarea. */
const TEXT_SOURCES = ['text', 'markdown', 'openapi'];
/** Source types that show the file drop zone (a queued file wins over typed text). */
const FILE_UPLOAD_SOURCES = [...FILE_ONLY_SOURCES, 'text', 'markdown'];
/** Extensions POST /knowledge/ingest/file accepts. Legacy .ppt is deliberately
 *  absent — the backend refuses it with 415 and asks for .pptx. */
const UPLOAD_EXTENSIONS = ['.txt', '.md', '.csv', '.py', '.ts', '.js', '.json', '.pdf', '.docx', '.xlsx', '.pptx', '.png', '.jpg', '.jpeg', '.webp'];
const NO_COLLECTION_MSG = 'Select a collection first.';

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function uploadErrorMessage(e: unknown, filename?: string): string {
  const prefix = filename ? `${filename}: ` : '';
  if (e instanceof ApiError && e.status === 429) {
    // Plan document quota or LLM budget: the server says which.
    return `${prefix}not ingested, limit reached (${e.message}). Raise the plan limit or budget, or free up space.`;
  }
  if (e instanceof ApiError) return `${prefix}Upload failed (${e.status}): ${e.message}`;
  return `${prefix}Upload failed: ${e instanceof Error ? e.message : String(e)}`;
}

// ── RPA URL Scraper ───────────────────────────────────────────────────────────

interface RpaUrlResult {
  url: string; success: boolean; chunks_ingested: number; total_chars?: number;
  playwright_used?: boolean; screenshot_captured?: boolean; links_extracted?: number; error?: string;
}
interface RpaIngestResp {
  collection_id: string; source_type: string; urls_processed: number; urls_succeeded: number;
  total_chunks_ingested: number; playwright_available: boolean; results: RpaUrlResult[];
}

function RpaScrapeSection({ collections }: { collections: Collection[] }) {
  const qc = useQueryClient();
  const [collectionId, setCollectionId] = useState('');
  const [urlsText, setUrlsText] = useState('');
  const [screenshot, setScreenshot] = useState(false);
  const [includeLinks, setIncludeLinks] = useState(false);
  const [rpaResult, setRpaResult] = useState<RpaIngestResp | null>(null);

  const urls = urlsText.split('\n').map((l) => l.trim()).filter((l) => l.startsWith('http'));

  const rpaMutation = useMutation({
    mutationFn: () => apiFetch<RpaIngestResp>('/knowledge/ingest/rpa-url', {
      method: 'POST',
      body: JSON.stringify({
        collection_id: collectionId, urls, screenshot,
        include_links: includeLinks, source_type: 'rpa-web', max_chars: 50000,
      }),
    }),
    onSuccess: (r) => {
      setRpaResult(r);
      void qc.invalidateQueries({ queryKey: ['knowledge-collections'] });
      toast({ kind: 'success', message: `Scraped ${r.urls_succeeded}/${r.urls_processed} URLs → ${r.total_chunks_ingested} chunks.` });
    },
    onError: (e) => toast({ kind: 'error', message: String(e) }),
  });

  return (
    <div data-testid="rpa-scrape-section" className="bg-gradient-to-br from-violet-50 to-blue-50 border-2 border-violet-200 rounded-xl p-5 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold flex items-center gap-2 text-violet-800">
            <Globe className="h-4 w-4" /> RPA Web Scraper
            <span className="text-xs bg-violet-100 text-violet-600 px-2 py-0.5 rounded-full font-normal">Playwright</span>
          </h3>
          <p className="text-xs text-violet-600 mt-0.5">
            Renders JavaScript-heavy pages including SPAs, React apps, and dynamic content.
          </p>
        </div>
        {rpaResult && (
          <div className={`flex items-center gap-1 text-xs px-2 py-1 rounded-full ${rpaResult.playwright_available ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'}`}>
            {rpaResult.playwright_available ? <CheckCircle className="h-3 w-3" /> : <Zap className="h-3 w-3" />}
            {rpaResult.playwright_available ? 'Playwright active' : 'httpx fallback'}
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-5 gap-3">
        <div className="sm:col-span-2">
          <label className="block text-xs font-medium text-violet-700 mb-1">Target collection *</label>
          <select value={collectionId} onChange={(e) => setCollectionId(e.target.value)}
            className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-background">
            <option value="">Select collection…</option>
            {collections.map((c) => <option key={c.collection_id} value={c.collection_id}>{c.name}</option>)}
          </select>
        </div>
        <div className="sm:col-span-3">
          <label className="block text-xs font-medium text-violet-700 mb-1">
            URLs to scrape <span className="font-normal text-muted-foreground">(one per line, max 20)</span>
          </label>
          <textarea data-testid="rpa-urls-input" value={urlsText} onChange={(e) => setUrlsText(e.target.value)}
            rows={4} placeholder={'https://docs.example.com/api\nhttps://blog.example.com/post-1'}
            className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-background font-mono resize-none" />
          <p className="text-xs text-violet-500 mt-0.5">{urls.length} valid URL{urls.length !== 1 ? 's' : ''} detected</p>
        </div>
      </div>

      <div className="flex flex-wrap gap-4">
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input type="checkbox" id="rpa-screenshot" checked={screenshot} onChange={(e) => setScreenshot(e.target.checked)} className="rounded" />
          <Eye className="h-3.5 w-3.5 text-violet-500" />
          <span>Capture screenshot</span>
        </label>
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input type="checkbox" checked={includeLinks} onChange={(e) => setIncludeLinks(e.target.checked)} className="rounded" />
          <Link className="h-3.5 w-3.5 text-violet-500" />
          <span>Extract page links</span>
        </label>
      </div>

      <button data-testid="rpa-scrape-btn" onClick={() => rpaMutation.mutate()}
        disabled={!collectionId || urls.length === 0 || rpaMutation.isPending}
        className="flex items-center gap-2 px-5 py-2.5 bg-violet-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 hover:bg-violet-700">
        {rpaMutation.isPending ? (
          <><Loader2 className="h-4 w-4 animate-spin" /> Scraping {urls.length} URL{urls.length !== 1 ? 's' : ''}…</>
        ) : (
          <><Globe className="h-4 w-4" /> Scrape with RPA</>
        )}
      </button>

      {rpaMutation.isPending && (
        <div className="flex items-center gap-3 text-sm text-violet-600 bg-violet-50 rounded-lg p-3">
          <Loader2 className="h-4 w-4 animate-spin shrink-0" />
          <p className="font-medium">Launching Playwright browser… Rendering pages, extracting text, chunking, embedding</p>
        </div>
      )}

      {rpaResult && (
        <div data-testid="rpa-results" className="space-y-2">
          <div className="flex items-center gap-3 text-sm">
            <span className="font-medium">{rpaResult.urls_succeeded}/{rpaResult.urls_processed} URLs scraped</span>
            <span className="text-muted-foreground">·</span>
            <span className="font-semibold text-violet-700">{rpaResult.total_chunks_ingested} chunks indexed</span>
          </div>
          {rpaResult.results.map((r) => (
            <div key={r.url} className={`flex items-start gap-2 text-xs p-2 rounded-lg ${r.success ? 'bg-green-50 border border-green-100' : 'bg-red-50 border border-red-100'}`}>
              {r.success ? <CheckCircle className="h-3.5 w-3.5 text-green-500 shrink-0 mt-0.5" /> : <XCircle className="h-3.5 w-3.5 text-red-500 shrink-0 mt-0.5" />}
              <div className="flex-1 min-w-0">
                <a href={r.url} target="_blank" rel="noreferrer" className="font-mono text-blue-600 hover:underline truncate block max-w-xs">{r.url}</a>
                {r.success ? (
                  <p className="text-green-600 mt-0.5">
                    {r.chunks_ingested} chunks · {r.total_chars?.toLocaleString()} chars
                    {r.playwright_used && ' · Playwright'}
                  </p>
                ) : (
                  <p className="text-red-600 mt-0.5">{r.error}</p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function IngestTab() {
  const [selectedSource, setSelectedSource] = useState('text');
  const [collectionId, setCollectionId] = useState('');
  const [content, setContent] = useState('');
  const [config, setConfig] = useState<Record<string, string>>({});
  // A picked/dropped file is only queued here; the Ingest button uploads it.
  const [queuedFile, setQueuedFile] = useState<File | null>(null);
  // Persistent (not a toast) so the user can read it and retry the same file.
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<{ filename: string; chunks: number } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();

  const { data: collections = [] } = useQuery<Collection[]>({
    queryKey: ['knowledge-collections'],
    queryFn: () => apiFetch('/knowledge/collections'),
  });

  const ingestMutation = useMutation({
    mutationFn: () => apiFetch<{ chunks_created: number; document_id: string }>('/knowledge/ingest', {
      method: 'POST',
      body: JSON.stringify({ collection_id: collectionId, source_type: selectedSource, content, source_config: config }),
    }),
    onSuccess: (r) => {
      toast({ kind: 'success', message: `Ingested successfully. ${r.chunks_created} chunks indexed.` });
      setContent('');
      void qc.invalidateQueries({ queryKey: ['knowledge-collections'] });
    },
    onError: (e) => toast({ kind: 'error', message: String(e) }),
  });

  const fileMutation = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('collection_id', collectionId);
      // The inline error + our own toast report failures, so silence the client's
      // generic 5xx toast (otherwise a 503 flashed two toasts).
      return apiFetch<{ chunks_created: number; filename: string; truncated?: boolean; truncated_sheets?: string[] }>('/knowledge/ingest/file', { method: 'POST', body: fd }, { silenceServerErrorToast: true });
    },
    onMutate: () => { setUploadError(null); setLastResult(null); },
    onSuccess: (r, file) => {
      const filename = r.filename || file.name;
      toast({ kind: 'success', message: `${filename}: ${r.chunks_created} chunks created.` });
      if (r.truncated) {
        const sheets = r.truncated_sheets?.length ? ` (sheets: ${r.truncated_sheets.join(', ')})` : '';
        toast({ kind: 'info', message: `${filename}: workbook truncated at the row/sheet limit${sheets}; only part of it was indexed.` });
      }
      setLastResult({ filename, chunks: r.chunks_created });
      setQueuedFile(null);
      void qc.invalidateQueries({ queryKey: ['knowledge-collections'] });
    },
    onError: (e, file) => {
      const message = uploadErrorMessage(e, file.name);
      setUploadError(message); // keep the queued file so Retry is one click
      toast({ kind: 'error', message });
    },
  });

  const queueFile = useCallback((file: File | undefined) => {
    if (!file) return;
    setQueuedFile(file);
    setUploadError(null);
    setLastResult(null);
    if (!collectionId) toast({ kind: 'error', message: NO_COLLECTION_MSG });
  }, [collectionId]);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    queueFile(e.dataTransfer.files[0]);
  }, [queueFile]);

  const showsDropzone = FILE_UPLOAD_SOURCES.includes(selectedSource);
  const sendsFile = showsDropzone && queuedFile !== null;
  const isPending = ingestMutation.isPending || fileMutation.isPending;
  let disabledReason: string | null = null;
  if (!collectionId) disabledReason = NO_COLLECTION_MSG;
  else if (FILE_ONLY_SOURCES.includes(selectedSource) && !queuedFile) disabledReason = 'Choose a file to upload.';
  else if (TEXT_SOURCES.includes(selectedSource) && !sendsFile && !content.trim())
    disabledReason = showsDropzone ? 'Enter some content or choose a file.' : 'Enter some content.';

  const submit = () => {
    if (disabledReason || isPending) return;
    if (sendsFile && queuedFile) fileMutation.mutate(queuedFile);
    else if (!FILE_ONLY_SOURCES.includes(selectedSource)) ingestMutation.mutate();
  };

  return (
    <div className="space-y-5">
      {/* RPA Scraper — shown first as the primary wow feature */}
      <RpaScrapeSection collections={collections} />

      {/* Divider */}
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <div className="flex-1 h-px bg-border" />
        <span>or ingest from other sources</span>
        <div className="flex-1 h-px bg-border" />
      </div>

      {/* Standard ingestion */}
      <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label className="block text-xs text-muted-foreground mb-1">Collection *</label>
          <select value={collectionId} onChange={(e) => setCollectionId(e.target.value)}
            className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background">
            <option value="">Select collection…</option>
            {collections.map((c) => <option key={c.collection_id} value={c.collection_id}>{c.name}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-xs text-muted-foreground mb-1">Source type</label>
          <div className="flex flex-wrap gap-1.5">
            {SOURCE_TYPES.map((s) => (
              <button key={s.value} onClick={() => setSelectedSource(s.value)}
                className={`px-2.5 py-1 rounded border text-xs transition-colors ${selectedSource === s.value ? 'border-primary bg-primary/10 text-primary' : 'border-border hover:bg-muted'}`}>
                {s.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {TEXT_SOURCES.includes(selectedSource) && (
        <div>
          <label className="block text-xs text-muted-foreground mb-1">Content *</label>
          <textarea value={content} onChange={(e) => setContent(e.target.value)} rows={8}
            placeholder={selectedSource === 'openapi' ? 'Paste OpenAPI JSON or YAML…' : 'Paste content to ingest…'}
            className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background font-mono resize-none" />
        </div>
      )}

      {['url', 'git'].includes(selectedSource) && (
        <div>
          <label className="block text-xs text-muted-foreground mb-1">URL / Repo URL *</label>
          <input value={config.url ?? ''} onChange={(e) => setConfig((c) => ({ ...c, url: e.target.value }))}
            placeholder="https://example.com/page" className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background" />
        </div>
      )}

      {selectedSource === 'github' && (
        <div className="grid grid-cols-2 gap-3">
          <input placeholder="owner/repo" value={config.repo ?? ''} onChange={(e) => setConfig((c) => ({ ...c, repo: e.target.value }))}
            className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background" />
          <input placeholder="Branch (default: HEAD)" value={config.branch ?? ''} onChange={(e) => setConfig((c) => ({ ...c, branch: e.target.value }))}
            className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background" />
        </div>
      )}

      {['confluence', 'jira'].includes(selectedSource) && (
        <div className="grid grid-cols-2 gap-3">
          <input placeholder="Base URL" value={config.base_url ?? ''} onChange={(e) => setConfig((c) => ({ ...c, base_url: e.target.value }))}
            className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background" />
          <input placeholder={selectedSource === 'confluence' ? 'Space key' : 'Project key'} value={config.space_key ?? config.project_key ?? ''}
            onChange={(e) => setConfig((c) => ({ ...c, [selectedSource === 'confluence' ? 'space_key' : 'project_key']: e.target.value }))}
            className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background" />
          <input type="password" placeholder="API token" value={config.api_token ?? ''} onChange={(e) => setConfig((c) => ({ ...c, api_token: e.target.value }))}
            className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background" />
        </div>
      )}

      {selectedSource === 'slack' && (
        <div className="grid grid-cols-2 gap-3">
          <input type="password" placeholder="Bot token" value={config.bot_token ?? ''} onChange={(e) => setConfig((c) => ({ ...c, bot_token: e.target.value }))}
            className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background" />
          <input placeholder="Channels (comma-separated)" value={config.channels ?? ''} onChange={(e) => setConfig((c) => ({ ...c, channels: e.target.value }))}
            className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background" />
        </div>
      )}

      {/* File upload zone */}
      {showsDropzone && (
        <div className="space-y-2">
          <div onDrop={onDrop} onDragOver={(e) => e.preventDefault()}
            className="border-2 border-dashed border-border rounded-xl p-6 flex flex-col items-center gap-2 cursor-pointer hover:border-[#00D4FF]/50 transition-colors"
            onClick={() => fileRef.current?.click()}>
            <Upload className="h-6 w-6 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">Drag & drop a file here, or click to browse</p>
            <p className="text-xs text-muted-foreground">{UPLOAD_EXTENSIONS.join(' ')}</p>
            <p className="text-xs text-muted-foreground">Legacy .ppt isn&apos;t supported — save as .pptx first.</p>
            <input ref={fileRef} type="file" accept={UPLOAD_EXTENSIONS.join(',')} className="hidden"
              data-testid="ingest-file-input"
              onChange={(e) => {
                queueFile(e.target.files?.[0]);
                // Reset so picking the same file again still fires onChange.
                e.target.value = '';
              }} />
          </div>
          {queuedFile && (
            <div data-testid="queued-file" className="flex items-center gap-2 px-3 py-2 border border-border rounded-md text-sm bg-muted/30">
              <FileText className="h-4 w-4 text-muted-foreground shrink-0" />
              <span className="truncate font-medium">{queuedFile.name}</span>
              <span className="text-xs text-muted-foreground shrink-0">{formatBytes(queuedFile.size)}</span>
              <span className="text-xs text-muted-foreground shrink-0">· queued</span>
              <button type="button" aria-label={`Remove ${queuedFile.name}`} disabled={fileMutation.isPending}
                onClick={() => { setQueuedFile(null); setUploadError(null); }}
                className="ml-auto p-1 rounded text-muted-foreground hover:text-red-500 disabled:opacity-50">
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          )}
        </div>
      )}

      {uploadError && queuedFile && showsDropzone && (
        <div role="alert" data-testid="ingest-error"
          className="flex items-start gap-2 px-3 py-2 border border-red-500/40 bg-red-500/10 rounded-md text-sm text-red-600">
          <XCircle className="h-4 w-4 mt-0.5 shrink-0" />
          <span className="flex-1">{uploadError}</span>
          <button type="button" onClick={submit} disabled={isPending || disabledReason !== null}
            className="shrink-0 flex items-center gap-1 px-2 py-0.5 border border-red-500/40 rounded text-xs hover:bg-red-500/10 disabled:opacity-50">
            <RefreshCw className="h-3 w-3" /> Retry
          </button>
        </div>
      )}

      {lastResult && (
        <div role="status" data-testid="ingest-result"
          className="flex items-center gap-2 px-3 py-2 border border-green-500/40 bg-green-500/10 rounded-md text-sm text-green-700">
          <CheckCircle className="h-4 w-4 shrink-0" />
          <span>{lastResult.filename}: {lastResult.chunks} chunks created.</span>
        </div>
      )}

      <div className="flex items-center gap-3">
        <button data-testid="ingest-submit" onClick={submit} disabled={disabledReason !== null || isPending}
          aria-describedby={disabledReason ? 'ingest-disabled-reason' : undefined}
          className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50">
          {isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Zap className="h-4 w-4" />}
          {fileMutation.isPending ? 'Uploading…' : ingestMutation.isPending ? 'Ingesting…' : 'Ingest'}
        </button>
        {disabledReason && !isPending && (
          <span id="ingest-disabled-reason" data-testid="ingest-disabled-reason" className="text-xs text-muted-foreground">
            {disabledReason}
          </span>
        )}
      </div>
      </div>
    </div>
  );
}

function SearchTab() {
  const [query, setQuery] = useState('');
  const [collectionId, setCollectionId] = useState('');
  const [topK, setTopK] = useState(10);
  const [results, setResults] = useState<SearchResult[]>([]);

  const { data: collections = [] } = useQuery<Collection[]>({
    queryKey: ['knowledge-collections'],
    queryFn: () => apiFetch('/knowledge/collections'),
  });

  const searchMutation = useMutation({
    mutationFn: () => {
      const params = new URLSearchParams({ q: query, top_k: String(topK) });
      if (collectionId) params.set('collection_id', collectionId);
      return apiFetch<SearchResult[]>(`/knowledge/search?${params.toString()}`);
    },
    onSuccess: (r) => setResults(Array.isArray(r) ? r : []),
    onError: (e) => toast({ kind: 'error', message: llmErrorMessage(e, 'Search failed') }),
  });

  function highlightMatch(text: string, q: string): string {
    if (!q.trim()) return text;
    const words = q.trim().split(/\s+/).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    const re = new RegExp(`(${words.join('|')})`, 'gi');
    return text.replace(re, '**$1**');
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div className="sm:col-span-2">
          <label className="block text-xs text-muted-foreground mb-1">Search query *</label>
          <input value={query} onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && query.trim()) searchMutation.mutate(); }}
            placeholder="Search across your knowledge base…"
            className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background" />
        </div>
        <div>
          <label className="block text-xs text-muted-foreground mb-1">Collection</label>
          <select value={collectionId} onChange={(e) => setCollectionId(e.target.value)}
            className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background">
            <option value="">All collections</option>
            {collections.map((c) => <option key={c.collection_id} value={c.collection_id}>{c.name}</option>)}
          </select>
        </div>
      </div>
      <div className="flex items-center gap-3">
        <label className="text-xs text-muted-foreground">Results: {topK}</label>
        <input type="range" min="3" max="20" value={topK} onChange={(e) => setTopK(Number(e.target.value))} className="w-24" />
        <button onClick={() => searchMutation.mutate()} disabled={!query.trim() || searchMutation.isPending}
          className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50 ml-auto">
          {searchMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
          Search
        </button>
      </div>
      {results.length > 0 ? (
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">{results.length} results</p>
          {results.map((r, i) => (
            <div key={r.doc_id ?? r.chunk_id ?? i} className="bg-card border border-border rounded-xl p-4 space-y-2">
              <div className="flex items-center justify-between gap-2">
                 <span className={`px-2 py-0.5 rounded text-xs font-medium ${r.score > 0.8 ? 'bg-green-100 text-green-700' : r.score > 0.6 ? 'bg-amber-100 text-amber-700' : 'bg-muted text-muted-foreground'}`}>
                  {(r.score * 100).toFixed(1)}% match
                </span>
                <div className="flex gap-1">
                  {r.source_url && <a href={r.source_url} target="_blank" rel="noreferrer" className="p-1 text-muted-foreground hover:text-blue-500"><ExternalLink className="h-3.5 w-3.5" /></a>}
                  <button onClick={() => { void navigator.clipboard.writeText(r.content); toast({ kind: 'success', message: 'Copied.' }); }}
                    className="p-1 text-muted-foreground hover:text-foreground"><ClipboardCopy className="h-3.5 w-3.5" /></button>
                </div>
              </div>
              <p className="text-sm leading-relaxed whitespace-pre-wrap">
                {highlightMatch(r.content, query).split('**').map((part, j) =>
                  j % 2 === 1 ? <mark key={j} className="bg-yellow-100 text-yellow-900 rounded px-0.5">{part}</mark> : part
                )}
              </p>
            </div>
          ))}
        </div>
      ) : searchMutation.isSuccess ? (
        <div className="flex flex-col items-center py-12 text-muted-foreground gap-2">
          <Search className="h-8 w-8 opacity-30" />
          <p className="text-sm">No results found.</p>
        </div>
      ) : null}
    </div>
  );
}

// ── Analytics Tab ─────────────────────────────────────────────────────────────

function AnalyticsTab() {
  const { data: collections = [] } = useQuery<Collection[]>({
    queryKey: ['knowledge-collections'],
    queryFn: () => apiFetch('/knowledge/collections'),
  });
  const { data: cacheStats } = useQuery<{ hits: number; misses: number }>({
    queryKey: ['knowledge-cache-stats'],
    queryFn: () => apiFetch('/knowledge/cache/stats'),
    staleTime: 30_000,
  });
  const { data: allStats, isLoading } = useQuery<CollectionStats[]>({
    queryKey: ['all-collection-stats'],
    queryFn: async () => {
      // Try bulk analytics endpoint first
      const bulk = await apiFetch<CollectionStats[]>('/knowledge/analytics').catch(() => null);
      if (Array.isArray(bulk) && bulk.length > 0) return bulk;
      // Fall back: throttled individual calls (max 3 concurrent)
      const CONCURRENCY = 3;
      const results: CollectionStats[] = [];
      for (let i = 0; i < collections.length; i += CONCURRENCY) {
        const batch = collections.slice(i, i + CONCURRENCY);
        const settled = await Promise.allSettled(
          batch.map((c) => apiFetch<CollectionStats>(`/knowledge/collections/${c.collection_id}/stats`))
        );
        settled.forEach((r) => { if (r.status === 'fulfilled') results.push(r.value); });
      }
      return results;
    },
    enabled: collections.length > 0,
    staleTime: 60_000,
  });

  const hitRate = cacheStats ? Math.round((cacheStats.hits / Math.max(cacheStats.hits + cacheStats.misses, 1)) * 100) : 0;

  return (
    <div className="space-y-5">
      {/* Cache stats */}
      {cacheStats && (
        <div className="grid grid-cols-3 gap-3">
          <div className="bg-card border border-border rounded-xl p-3">
            <p className="text-xs text-muted-foreground">Cache Hits</p>
            <p className="text-2xl font-bold text-green-600">{cacheStats.hits}</p>
          </div>
          <div className="bg-card border border-border rounded-xl p-3">
            <p className="text-xs text-muted-foreground">Cache Misses</p>
            <p className="text-2xl font-bold text-amber-600">{cacheStats.misses}</p>
          </div>
          <div className="bg-card border border-border rounded-xl p-3">
            <p className="text-xs text-muted-foreground">Hit Rate</p>
            <p className="text-2xl font-bold">{hitRate}%</p>
          </div>
        </div>
      )}

      {/* Collection health grid */}
      <div className="bg-card border border-border rounded-xl p-4">
        <h3 className="font-medium text-sm mb-4 flex items-center gap-2"><BarChart2 className="h-4 w-4" /> Collection Health</h3>
        {isLoading ? <Loader2 className="h-5 w-5 animate-spin" /> : (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {(allStats ?? []).map((s) => (
              <div key={s.collection_id} className="flex items-center gap-3 border border-border rounded-lg p-3">
                <HealthGauge score={s.health_score} />
                <div className="text-xs space-y-0.5">
                  <p className="font-medium truncate max-w-[100px]">{s.name}</p>
                  <p className="text-muted-foreground">{s.chunk_count} chunks</p>
                  <p className="text-muted-foreground">{s.embedding_coverage_pct}% embedded</p>
                </div>
              </div>
            ))}
            {(!allStats || allStats.length === 0) && (
              <p className="text-sm text-muted-foreground col-span-3 py-4 text-center">No collections to analyze.</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Documents Tab ─────────────────────────────────────────────────────────────

function DocumentsTab({ collections }: { collections: Collection[] }) {
  const qc = useQueryClient();
  const [selectedCollection, setSelectedCollection] = useState<string>(
    collections[0]?.collection_id ?? ''
  );
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [sourceTypeFilter, setSourceTypeFilter] = useState<string | null>(null);
  const PAGE_SIZE = 20;

  // A failed listing is an error state, never an empty collection: the old
  // `.catch(() => ({ documents: [], total: 0 }))` turned every 503 into
  // "No documents in this collection" (KB-UI-HIDES-ERRORS).
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['knowledge-docs', selectedCollection, page, search],
    queryFn: () =>
      apiFetch<{ documents: Record<string, unknown>[]; total: number; total_capped?: boolean }>(
        `/knowledge/collections/${selectedCollection}/documents?limit=${PAGE_SIZE}&offset=${(page - 1) * PAGE_SIZE}${search ? `&search=${encodeURIComponent(search)}` : ''}`
      ),
    enabled: !!selectedCollection,
    staleTime: 30_000,
  });
  const listError = isError
    ? (error instanceof Error && error.message ? error.message : 'Unknown error')
    : null;

  // Source-provenance filtering (WS-13): the backend `/documents` endpoint has
  // no `source_type` query param, so this filters the real `source_type` field
  // already returned per document (client-side, over the currently loaded
  // page — every value shown here comes straight from the API response).
  const allDocuments = data?.documents ?? [];
  const sourceTypes = [...new Set(allDocuments.map((d) => (d.source_type as string | undefined) ?? 'unknown'))].sort();
  const filteredDocuments = sourceTypeFilter
    ? allDocuments.filter((d) => ((d.source_type as string | undefined) ?? 'unknown') === sourceTypeFilter)
    : allDocuments;

  const [previewDoc, setPreviewDoc] = useState<Record<string, unknown> | null>(null);

  const deleteMutation = useMutation({
    mutationFn: (docId: string) =>
      apiFetch(`/knowledge/collections/${selectedCollection}/documents/${docId}`, { method: 'DELETE' }),
    onSuccess: () => {
      toast({ kind: 'success', message: 'Document deleted' });
      void qc.invalidateQueries({ queryKey: ['knowledge-docs', selectedCollection] });
    },
    onError: (e) => toast({
      kind: 'error',
      message: e instanceof ApiError && e.status === 409
        ? 'This document is under legal hold and cannot be deleted.'
        : 'Delete failed',
    }),
  });

  const reingestMutation = useMutation({
    mutationFn: (docId: string) =>
      apiFetch(`/knowledge/collections/${selectedCollection}/documents/${docId}/reingest`, {
        method: 'POST',
      }),
    onSuccess: () => {
      toast({ kind: 'success', message: 'Document queued for re-ingestion' });
      void qc.invalidateQueries({ queryKey: ['knowledge-docs', selectedCollection] });
    },
    onError: () => toast({ kind: 'error', message: 'Re-ingest failed' }),
  });

  const syncMutation = useMutation({
    mutationFn: () =>
      apiFetch(`/knowledge/collections/${selectedCollection}/sync`, { method: 'POST' }),
    onSuccess: () => toast({ kind: 'success', message: 'Collection sync started' }),
    onError: () => toast({ kind: 'info', message: 'Sync not available for this collection type' }),
  });

  if (!selectedCollection) {
    return (
      <div className="flex flex-col items-center justify-center h-32 text-muted-foreground">
        <Database className="h-8 w-8 opacity-20 mb-2" />
        <p className="text-sm">Create a collection first to browse documents.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Collection selector + search */}
      <div className="flex items-center gap-3 flex-wrap">
        <select
          value={selectedCollection}
          onChange={(e) => { setSelectedCollection(e.target.value); setPage(1); }}
          className="px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary"
        >
          {collections.map((c) => (
            <option key={c.collection_id} value={c.collection_id}>
              {c.name} ({c.doc_count ?? 0} docs)
            </option>
          ))}
        </select>

        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <input
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); }}
            placeholder="Search documents…"
            className="w-full pl-9 pr-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary"
          />
        </div>

        {!listError && !isLoading && (
          <span className="text-xs text-muted-foreground">
            {data?.total ?? 0}{data?.total_capped ? '+' : ''} documents
          </span>
        )}

        <button
          onClick={() => syncMutation.mutate()}
          disabled={syncMutation.isPending || !selectedCollection}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-input rounded-lg hover:bg-muted/50 disabled:opacity-50 transition-colors"
          title="Sync all documents from their source"
        >
          {syncMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
          Sync All
        </button>
      </div>

      {/* Source-provenance filter — every value is a real source_type already
          present on the loaded documents (incl. rpa-web, ocr, pdf, docx, …). */}
      {sourceTypes.length > 1 && (
        <div className="flex items-center gap-1.5 flex-wrap" role="group" aria-label="Filter by source">
          <span className="text-xs text-muted-foreground">Source:</span>
          <button
            onClick={() => setSourceTypeFilter(null)}
            aria-pressed={!sourceTypeFilter}
            className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${!sourceTypeFilter ? 'bg-primary text-primary-foreground border-primary' : 'border-border hover:bg-muted'}`}
          >
            All
          </button>
          {sourceTypes.map((st) => (
            <button
              key={st}
              onClick={() => setSourceTypeFilter(st === sourceTypeFilter ? null : st)}
              aria-pressed={st === sourceTypeFilter}
              className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${st === sourceTypeFilter ? 'bg-primary text-primary-foreground border-primary' : 'border-border hover:bg-muted'}`}
            >
              {st}
            </button>
          ))}
        </div>
      )}

      {/* Document list */}
      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-16 rounded-lg" />
          ))}
        </div>
      ) : listError ? (
        <div
          role="alert"
          className="flex flex-col items-center justify-center gap-2 h-32 border border-destructive/40 bg-destructive/5 rounded-xl text-center px-4"
        >
          <XCircle className="h-6 w-6 text-destructive" />
          <p className="text-sm font-medium text-destructive">Could not load documents</p>
          <p className="text-xs text-muted-foreground">{listError}</p>
          <button
            onClick={() => void refetch()}
            disabled={isFetching}
            className="flex items-center gap-1.5 px-3 py-1 text-xs border border-input rounded-lg hover:bg-muted/50 disabled:opacity-50"
          >
            {isFetching ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            Retry
          </button>
        </div>
      ) : filteredDocuments.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-32 text-muted-foreground">
          <FileText className="h-8 w-8 opacity-20 mb-2" />
          <p className="text-sm">
            {sourceTypeFilter
              ? `No ${sourceTypeFilter} documents on this page`
              : search ? 'No documents match your search' : 'No documents in this collection'}
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {filteredDocuments.map((doc) => {
            const docId = (doc.id ?? doc.document_id) as string;
            const docTitle = (doc.title ?? doc.source ?? (docId?.slice(0, 20))) as string | undefined;
            const chunkCount = (doc.chunk_count ?? 0) as number;
            const createdAt = doc.created_at as string | undefined;
            const sourceType = doc.source_type as string | undefined;
            const preview = doc.preview as string | undefined;

            return (
              <div
                key={docId}
                className="flex items-start gap-3 p-4 border border-border rounded-xl hover:border-[#00D4FF]/30 transition-colors group"
              >
                <div className="p-2 bg-primary/10 rounded-lg shrink-0">
                  <FileText className="h-4 w-4 text-[#00D4FF]" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">
                    {docTitle ?? 'Untitled'}
                  </p>
                  <div className="flex items-center gap-3 mt-0.5">
                    <span className="text-xs text-muted-foreground">{chunkCount} chunks</span>
                    {createdAt && (
                      <span className="text-xs text-muted-foreground">
                        Added {new Date(createdAt).toLocaleDateString()}
                      </span>
                    )}
                    {sourceType && (
                      <span className="text-xs bg-primary/10 text-primary px-1.5 py-0.5 rounded">
                        {sourceType}
                      </span>
                    )}
                  </div>
                  {preview && (
                    <p className="text-xs text-muted-foreground mt-1 line-clamp-1 font-mono">
                      {preview}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                  <button
                    onClick={() => setPreviewDoc(doc)}
                    className="p-1.5 text-muted-foreground hover:text-foreground rounded hover:bg-muted/70"
                    title="Preview"
                  >
                    <Eye className="h-4 w-4" />
                  </button>
                  <button
                    onClick={() => reingestMutation.mutate(docId)}
                    disabled={reingestMutation.isPending}
                    className="p-1.5 text-muted-foreground hover:text-primary rounded hover:bg-primary/10 transition-colors disabled:opacity-50"
                    title="Re-ingest document from source"
                  >
                    {reingestMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                  </button>
                  <button
                    onClick={() => deleteMutation.mutate(docId)}
                    disabled={deleteMutation.isPending}
                    className="p-1.5 text-muted-foreground hover:text-destructive rounded hover:bg-red-50 dark:hover:bg-red-900/20 disabled:opacity-50"
                    title="Delete"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Pagination */}
      {(data?.total ?? 0) > PAGE_SIZE && (
        <Pagination
          page={page}
          pageSize={PAGE_SIZE}
          total={data?.total ?? 0}
          onPageChange={setPage}
        />
      )}

      {/* Document preview modal */}
      {previewDoc && (
        <div className="fixed inset-0 z-[200] flex items-center justify-center p-4">
          <div
            className="absolute inset-0 bg-black/50 backdrop-blur-sm"
            onClick={() => setPreviewDoc(null)}
          />
          <div className="relative bg-card border border-border rounded-xl shadow-xl max-w-2xl w-full max-h-[80vh] flex flex-col overflow-hidden">
            <div className="flex items-center justify-between px-5 py-4 border-b border-border">
              <h3 className="text-base font-semibold truncate">
                {(previewDoc.title ?? previewDoc.source ?? 'Document Preview') as string}
              </h3>
              <button onClick={() => setPreviewDoc(null)} className="text-muted-foreground hover:text-foreground">
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-5">
              <pre className="text-xs font-mono whitespace-pre-wrap text-muted-foreground">
                {(previewDoc.content ?? previewDoc.preview) != null
                  ? String(previewDoc.content ?? previewDoc.preview)
                  : JSON.stringify(previewDoc, null, 2)}
              </pre>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export function KnowledgePage() {
  const [activeTab, setActiveTab] = useState<Tab>('collections');

  const { data: collections = [] } = useQuery<Collection[]>({
    queryKey: ['knowledge-collections'],
    queryFn: () => apiFetch('/knowledge/collections'),
  });

  const tabs: Array<{ id: Tab; label: string; icon: React.ReactNode }> = [
    { id: 'collections', label: 'Collections', icon: <BookOpen className="h-4 w-4" /> },
    { id: 'ask',         label: 'Ask AI',      icon: <Sparkles className="h-4 w-4 text-violet-500" /> },
    { id: 'ingest',      label: 'Ingest',      icon: <Upload className="h-4 w-4" /> },
    { id: 'search',      label: 'Search',      icon: <Search className="h-4 w-4" /> },
    { id: 'analytics',   label: 'Analytics',   icon: <BarChart2 className="h-4 w-4" /> },
    { id: 'documents',   label: 'Documents',   icon: <FileText className="h-4 w-4" /> },
  ];

  return (
    <JARVISPageShell>
    <JARVISStagger className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Brain className="h-6 w-6 text-violet-500" /> Knowledge
        </h1>
        <p className="text-muted-foreground text-sm mt-1">
          Ingest documents · Search with AI · Ask questions with citations · Monitor collection health
        </p>
      </div>

      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="flex border-b border-border overflow-x-auto">
          {tabs.map((t) => (
            <button key={t.id} data-testid={`tab-${t.id}`} onClick={() => setActiveTab(t.id)}
              className={`flex items-center gap-2 px-5 py-3 text-sm font-medium border-b-2 whitespace-nowrap transition-colors ${
                activeTab === t.id ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}>
              {t.icon} {t.label}
            </button>
          ))}
        </div>
        <div className="p-5">
          {activeTab === 'collections' && <CollectionsTab />}
          {activeTab === 'ask'         && <AskAITab />}
          {activeTab === 'ingest'      && <IngestTab />}
          {activeTab === 'search'      && <SearchTab />}
          {activeTab === 'analytics'   && <AnalyticsTab />}
          {activeTab === 'documents'   && <DocumentsTab collections={collections} />}
        </div>
      </div>
    </JARVISStagger>
    </JARVISPageShell>
  );
}

export default KnowledgePage;
