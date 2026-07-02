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
  Database, ExternalLink, FileText, Loader2, MessageSquare, Plus,
  RefreshCw, Search, Sparkles, Trash2, Upload, Zap, XCircle,
} from 'lucide-react';
import { useAuthStore } from '@/stores/auth';
import { toast } from '@/stores/toast';

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
type Tab = 'collections' | 'ask' | 'ingest' | 'search' | 'analytics';

// ── Helpers ───────────────────────────────────────────────────────────────────

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const apiKey = useAuthStore.getState().apiKey;
  const headers: Record<string, string> = {};
  if (apiKey) headers['X-API-Key'] = apiKey;
  if (!(init?.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  return fetch(`${API_BASE}${path}`, { ...init, headers: { ...headers, ...(init?.headers ?? {}) } })
    .then(async (r) => { if (!r.ok) throw new Error(await r.text().catch(() => r.statusText)); return r.json() as Promise<T>; });
}

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

// ── Collections Tab ───────────────────────────────────────────────────────────

function CollectionsTab() {
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newEmbedder, setNewEmbedder] = useState('voyage');
  const [expanded, setExpanded] = useState<string | null>(null);

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
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['knowledge-collections'] }),
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
                <button data-testid={`delete-collection-${c.collection_id}`} onClick={() => deleteMutation.mutate(c.collection_id)}
                  className="p-1.5 text-muted-foreground hover:text-red-500 rounded">
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Ask AI Tab ────────────────────────────────────────────────────────────────

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
    mutationFn: (q: string) => apiFetch<RagAnswer>('/knowledge/chat', {
      method: 'POST',
      body: JSON.stringify({ question: q, collection_ids: selectedCollections, top_k: 5 }),
    }),
    onSuccess: (r, q) => {
      setHistory((h) => [{ q, a: r }, ...h.slice(0, 4)]);
      setQuestion('');
      setTimeout(() => answerRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 100);
    },
    onError: (e) => toast({ kind: 'error', message: String(e) }),
  });

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
          onKeyDown={(e) => { if (e.key === 'Enter' && e.metaKey && question.trim()) askMutation.mutate(question); }}
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
        <button data-testid="ask-btn" onClick={() => askMutation.mutate(question)} disabled={!question.trim() || askMutation.isPending}
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
              <div className="p-4">
                <p className="text-sm leading-relaxed whitespace-pre-wrap">{a.answer}</p>
              </div>
              {a.citations.length > 0 && (
                <div data-testid="citations-panel" className="border-t border-border px-4 py-3 bg-muted/20 space-y-2">
                  <p className="text-xs font-medium text-muted-foreground">Sources</p>
                  {a.citations.map((c) => (
                    <div key={c.index} className="flex items-start gap-2 text-xs">
                      <span className="bg-violet-100 text-violet-700 px-1.5 py-0.5 rounded font-mono shrink-0">[{c.index}]</span>
                      <div className="flex-1 min-w-0">
                        <p className="text-muted-foreground line-clamp-2">{c.excerpt}</p>
                        <div className="flex items-center gap-2 mt-0.5">
                          <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${c.score > 0.8 ? 'bg-green-100 text-green-700' : c.score > 0.6 ? 'bg-amber-100 text-amber-700' : 'bg-gray-100 text-gray-600'}`}>
                            {(c.score * 100).toFixed(0)}%
                          </span>
                          {c.source_url && <a href={c.source_url} target="_blank" rel="noreferrer" className="flex items-center gap-0.5 text-blue-500 hover:underline"><ExternalLink className="h-3 w-3" />source</a>}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
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
  { value: 'pdf', label: 'PDF' }, { value: 'docx', label: 'DOCX' }, { value: 'git', label: 'Git' },
  { value: 'github', label: 'GitHub' }, { value: 'openapi', label: 'OpenAPI' }, { value: 'confluence', label: 'Confluence' },
  { value: 'jira', label: 'Jira' }, { value: 'slack', label: 'Slack' },
];

function IngestTab() {
  const [selectedSource, setSelectedSource] = useState('text');
  const [collectionId, setCollectionId] = useState('');
  const [content, setContent] = useState('');
  const [config, setConfig] = useState<Record<string, string>>({});
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
      return apiFetch<{ chunks_created: number; filename: string }>('/knowledge/ingest/file', { method: 'POST', body: fd });
    },
    onSuccess: (r) => toast({ kind: 'success', message: `${r.filename}: ${r.chunks_created} chunks.` }),
    onError: (e) => toast({ kind: 'error', message: String(e) }),
  });

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file && collectionId) fileMutation.mutate(file);
    else if (!collectionId) toast({ kind: 'error', message: 'Select a collection first.' });
  }, [collectionId, fileMutation]);

  return (
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

      {['text', 'markdown', 'openapi'].includes(selectedSource) && (
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
      {['pdf', 'docx', 'text', 'markdown'].includes(selectedSource) && (
        <div onDrop={onDrop} onDragOver={(e) => e.preventDefault()}
          className="border-2 border-dashed border-border rounded-xl p-6 flex flex-col items-center gap-2 cursor-pointer hover:border-primary/50 transition-colors"
          onClick={() => fileRef.current?.click()}>
          <Upload className="h-6 w-6 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">Drag & drop a file here, or click to browse</p>
          <p className="text-xs text-muted-foreground">.txt .md .py .ts .js .json .pdf .docx</p>
          {fileMutation.isPending && <span className="text-xs text-primary">Uploading…</span>}
          <input ref={fileRef} type="file" accept=".txt,.md,.py,.ts,.js,.json,.pdf,.docx" className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f && collectionId) fileMutation.mutate(f); }} />
        </div>
      )}

      <button onClick={() => ingestMutation.mutate()} disabled={!collectionId || (!content.trim() && ['text', 'markdown', 'openapi'].includes(selectedSource)) || ingestMutation.isPending}
        className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50">
        {ingestMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Zap className="h-4 w-4" />}
        {ingestMutation.isPending ? 'Ingesting…' : 'Ingest'}
      </button>
    </div>
  );
}

// ── Search Tab ────────────────────────────────────────────────────────────────

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
    onError: (e) => toast({ kind: 'error', message: String(e) }),
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
                <span className={`px-2 py-0.5 rounded text-xs font-medium ${r.score > 0.8 ? 'bg-green-100 text-green-700' : r.score > 0.6 ? 'bg-amber-100 text-amber-700' : 'bg-gray-100 text-gray-600'}`}>
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
    queryFn: async () => Promise.all(collections.map((c) => apiFetch<CollectionStats>(`/knowledge/collections/${c.collection_id}/stats`))),
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

// ── Main Page ─────────────────────────────────────────────────────────────────

export function KnowledgePage() {
  const [activeTab, setActiveTab] = useState<Tab>('collections');

  const tabs: Array<{ id: Tab; label: string; icon: React.ReactNode }> = [
    { id: 'collections', label: 'Collections', icon: <BookOpen className="h-4 w-4" /> },
    { id: 'ask',         label: 'Ask AI',      icon: <Sparkles className="h-4 w-4 text-violet-500" /> },
    { id: 'ingest',      label: 'Ingest',      icon: <Upload className="h-4 w-4" /> },
    { id: 'search',      label: 'Search',      icon: <Search className="h-4 w-4" /> },
    { id: 'analytics',   label: 'Analytics',   icon: <BarChart2 className="h-4 w-4" /> },
  ];

  return (
    <div className="space-y-5">
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
        </div>
      </div>
    </div>
  );
}

export default KnowledgePage;
