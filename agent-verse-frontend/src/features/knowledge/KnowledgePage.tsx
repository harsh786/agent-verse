import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '@/stores/auth';

const API_BASE = (import.meta as any).env?.VITE_API_URL ?? 'http://localhost:8000';

// ── Types ─────────────────────────────────────────────────────────────────────

interface Collection {
  collection_id: string;
  name: string;
  doc_count?: number;
  embedder?: string;
  created_at?: string;
}

interface SearchResult {
  doc_id?: string;
  content: string;
  score: number;
  metadata?: Record<string, unknown>;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const hdrs = (apiKey: string) => ({
  'X-API-Key': apiKey,
  'Content-Type': 'application/json',
});

async function apiFetch<T>(apiKey: string, path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { headers: hdrs(apiKey), ...init });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ── Collections tab ──────────────────────────────────────────────────────────

function CollectionsTab({
  apiKey,
  collections,
  isLoading,
  error,
}: {
  apiKey: string;
  collections: Collection[];
  isLoading: boolean;
  error: Error | null;
}) {
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ name: '', embedder: '' });

  const createMutation = useMutation({
    mutationFn: () =>
      apiFetch<Collection>(apiKey, '/knowledge/collections', {
        method: 'POST',
        body: JSON.stringify({
          name: form.name,
          ...(form.embedder ? { embedder: form.embedder } : {}),
        }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['collections'] });
      setForm({ name: '', embedder: '' });
      setShowCreate(false);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch<void>(apiKey, `/knowledge/collections/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['collections'] }),
  });

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <button
          onClick={() => setShowCreate((v) => !v)}
          className="bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm hover:opacity-90"
        >
          {showCreate ? 'Cancel' : '+ New Collection'}
        </button>
      </div>

      {showCreate && (
        <div className="bg-card border border-border rounded-xl p-4 space-y-3">
          <h3 className="font-medium text-sm">New Collection</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium mb-1">Name</label>
              <input
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="my-knowledge-base"
                className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <div>
              <label className="block text-xs font-medium mb-1">Embedder (optional)</label>
              <input
                value={form.embedder}
                onChange={(e) => setForm((f) => ({ ...f, embedder: e.target.value }))}
                placeholder="openai/text-embedding-3-small"
                className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
          </div>
          {createMutation.isError && (
            <p className="text-xs text-red-600">{String(createMutation.error)}</p>
          )}
          <div className="flex justify-end">
            <button
              onClick={() => createMutation.mutate()}
              disabled={!form.name.trim() || createMutation.isPending}
              className="bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm disabled:opacity-50"
            >
              {createMutation.isPending ? 'Creating…' : 'Create'}
            </button>
          </div>
        </div>
      )}

      <div className="bg-card border border-border rounded-xl overflow-hidden">
        {isLoading ? (
          <div className="py-10 text-center text-sm text-muted-foreground">Loading…</div>
        ) : error ? (
          <div className="py-10 text-center text-sm text-red-500">Failed to load collections.</div>
        ) : collections.length === 0 ? (
          <div className="py-10 text-center text-sm text-muted-foreground">
            No collections yet. Create one to start ingesting documents.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                {['Name', 'Documents', 'Embedder', 'Created', ''].map((h) => (
                  <th key={h} className="text-left px-4 py-3 font-medium text-muted-foreground">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {collections.map((c) => (
                <tr key={c.collection_id} className="hover:bg-accent/50 transition-colors">
                  <td className="px-4 py-3 font-medium">{c.name}</td>
                  <td className="px-4 py-3 text-muted-foreground">{c.doc_count ?? '—'}</td>
                  <td className="px-4 py-3 text-xs text-muted-foreground font-mono">
                    {c.embedder ?? 'default'}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">
                    {c.created_at ? new Date(c.created_at).toLocaleDateString() : '—'}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={() => deleteMutation.mutate(c.collection_id)}
                      disabled={deleteMutation.isPending}
                      className="text-destructive hover:opacity-70 text-sm disabled:opacity-40"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ── Ingest tab ────────────────────────────────────────────────────────────────

const SOURCE_TYPES = [
  { value: 'text', label: 'Plain Text' },
  { value: 'markdown', label: 'Markdown' },
  { value: 'url', label: 'URL / Web Page' },
  { value: 'pdf', label: 'PDF File' },
  { value: 'docx', label: 'Word Document (.docx)' },
  { value: 'git', label: 'Git Repository' },
  { value: 'github', label: 'GitHub (repo/issues/PRs)' },
  { value: 'openapi', label: 'OpenAPI Schema' },
  { value: 'confluence', label: 'Confluence' },
  { value: 'jira', label: 'Jira' },
  { value: 'slack', label: 'Slack' },
];

function SourceConfigFields({
  sourceType,
  config,
  onChange,
}: {
  sourceType: string;
  config: Record<string, string>;
  onChange: (key: string, value: string) => void;
}) {
  switch (sourceType) {
    case 'url':
      return (
        <div className="space-y-2">
          <label className="block text-sm font-medium">
            URL to crawl
            <input
              aria-label="url to crawl"
              className="mt-1 block w-full rounded border px-3 py-2 text-sm bg-background"
              placeholder="https://docs.example.com"
              value={config.url ?? ''}
              onChange={(e) => onChange('url', e.target.value)}
            />
          </label>
          <label className="block text-sm font-medium">
            Max depth
            <input
              type="number"
              className="mt-1 block w-full rounded border px-3 py-2 text-sm bg-background"
              placeholder="3"
              value={config.max_depth ?? ''}
              onChange={(e) => onChange('max_depth', e.target.value)}
            />
          </label>
        </div>
      );
    case 'github':
      return (
        <div className="space-y-2">
          <label className="block text-sm font-medium">
            Repository (owner/repo)
            <input
              className="mt-1 block w-full rounded border px-3 py-2 text-sm bg-background"
              placeholder="acme/my-repo"
              value={config.repo ?? ''}
              onChange={(e) => onChange('repo', e.target.value)}
            />
          </label>
          <label className="block text-sm font-medium">
            Include (comma-separated: code, issues, prs)
            <input
              className="mt-1 block w-full rounded border px-3 py-2 text-sm bg-background"
              placeholder="code,issues"
              value={config.include ?? ''}
              onChange={(e) => onChange('include', e.target.value)}
            />
          </label>
        </div>
      );
    case 'confluence':
      return (
        <div className="space-y-2">
          <label className="block text-sm font-medium">
            Confluence URL
            <input
              aria-label="confluence url"
              className="mt-1 block w-full rounded border px-3 py-2 text-sm bg-background"
              placeholder="https://myorg.atlassian.net/wiki"
              value={config.base_url ?? ''}
              onChange={(e) => onChange('base_url', e.target.value)}
            />
          </label>
          <label className="block text-sm font-medium">
            Space key
            <input
              aria-label="space key"
              className="mt-1 block w-full rounded border px-3 py-2 text-sm bg-background"
              placeholder="ENG"
              value={config.space_key ?? ''}
              onChange={(e) => onChange('space_key', e.target.value)}
            />
          </label>
          <label className="block text-sm font-medium">
            API token
            <input
              type="password"
              className="mt-1 block w-full rounded border px-3 py-2 text-sm bg-background"
              value={config.api_token ?? ''}
              onChange={(e) => onChange('api_token', e.target.value)}
            />
          </label>
        </div>
      );
    case 'jira':
      return (
        <div className="space-y-2">
          <label className="block text-sm font-medium">
            Jira URL
            <input
              className="mt-1 block w-full rounded border px-3 py-2 text-sm bg-background"
              placeholder="https://myorg.atlassian.net"
              value={config.base_url ?? ''}
              onChange={(e) => onChange('base_url', e.target.value)}
            />
          </label>
          <label className="block text-sm font-medium">
            Project key
            <input
              className="mt-1 block w-full rounded border px-3 py-2 text-sm bg-background"
              placeholder="ENG"
              value={config.project_key ?? ''}
              onChange={(e) => onChange('project_key', e.target.value)}
            />
          </label>
          <label className="block text-sm font-medium">
            API token
            <input
              type="password"
              className="mt-1 block w-full rounded border px-3 py-2 text-sm bg-background"
              value={config.api_token ?? ''}
              onChange={(e) => onChange('api_token', e.target.value)}
            />
          </label>
        </div>
      );
    case 'slack':
      return (
        <div className="space-y-2">
          <label className="block text-sm font-medium">
            Bot token
            <input
              type="password"
              className="mt-1 block w-full rounded border px-3 py-2 text-sm bg-background"
              value={config.bot_token ?? ''}
              onChange={(e) => onChange('bot_token', e.target.value)}
            />
          </label>
          <label className="block text-sm font-medium">
            Channels (comma-separated)
            <input
              className="mt-1 block w-full rounded border px-3 py-2 text-sm bg-background"
              placeholder="#general,#eng"
              value={config.channels ?? ''}
              onChange={(e) => onChange('channels', e.target.value)}
            />
          </label>
        </div>
      );
    default:
      return null;
  }
}

function IngestTab({
  apiKey,
  collections,
}: {
  apiKey: string;
  collections: Collection[];
}) {
  const [form, setForm] = useState({
    collection_id: '',
    source_type: 'text',
    content: '',
  });
  const [sourceConfig, setSourceConfig] = useState<Record<string, string>>({});
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadStatus, setUploadStatus] = useState<string>('');

  const handleConfigChange = (key: string, value: string) => {
    setSourceConfig((c) => ({ ...c, [key]: value }));
  };

  const ingestMutation = useMutation({
    mutationFn: () =>
      apiFetch<{ doc_count: number }>(apiKey, '/knowledge/ingest', {
        method: 'POST',
        body: JSON.stringify({
          ...form,
          ...(Object.keys(sourceConfig).length > 0 ? { source_config: sourceConfig } : {}),
        }),
      }),
  });

  const uploadMutation = useMutation({
    mutationFn: async () => {
      if (!uploadFile || !form.collection_id) return;
      const formData = new FormData();
      formData.append('file', uploadFile);
      formData.append('collection_id', form.collection_id);
      const res = await fetch(`${API_BASE}/knowledge/ingest/file`, {
        method: 'POST',
        headers: { 'X-API-Key': apiKey },
        body: formData,
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return res.json();
    },
    onSuccess: (data: any) =>
      setUploadStatus(`✅ Ingested ${data?.chunks_created ?? 0} chunks`),
    onError: (err) => setUploadStatus(`❌ ${String(err)}`),
  });

  return (
    <div className="space-y-4 max-w-2xl">
      <div className="bg-card border border-border rounded-xl p-5 space-y-4">
        <h3 className="font-medium text-sm">Ingest Document</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-medium mb-1">Collection</label>
            <select
              value={form.collection_id}
              onChange={(e) => setForm((f) => ({ ...f, collection_id: e.target.value }))}
              className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
            >
              <option value="">Select a collection…</option>
              {collections.map((c) => (
                <option key={c.collection_id} value={c.collection_id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="source-type-select" className="block text-xs font-medium mb-1">Source Type</label>
            <select
              id="source-type-select"
              aria-label="Source Type"
              value={form.source_type}
              onChange={(e) => {
                setForm((f) => ({ ...f, source_type: e.target.value }));
                setSourceConfig({});
              }}
              className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
            >
              {SOURCE_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Per-source config fields */}
        {['url', 'github', 'confluence', 'jira', 'slack'].includes(form.source_type) && (
          <div className="p-3 rounded-lg bg-muted/50 border">
            <SourceConfigFields
              sourceType={form.source_type}
              config={sourceConfig}
              onChange={handleConfigChange}
            />
          </div>
        )}

        {/* File upload section */}
        <div>
          <label className="block text-xs font-medium mb-1">Upload File</label>
          <div
            className="border-2 border-dashed border-border rounded-xl p-6 text-center cursor-pointer hover:bg-accent/30 transition-colors"
            onClick={() => document.getElementById('file-upload')?.click()}
          >
            <input
              id="file-upload"
              type="file"
              accept=".txt,.md,.py,.ts,.js,.json,.pdf,.docx"
              className="hidden"
              onChange={(e) => {
                setUploadFile(e.target.files?.[0] || null);
                setUploadStatus('');
              }}
            />
            {uploadFile ? (
              <p className="text-sm font-medium">
                {uploadFile.name} ({(uploadFile.size / 1024).toFixed(1)} KB)
              </p>
            ) : (
              <p className="text-sm text-muted-foreground">
                Click to upload file (PDF, DOCX, TXT, MD, PY, TS)
              </p>
            )}
          </div>
          {uploadFile && (
            <div className="flex gap-2 mt-2">
              <button
                onClick={() => uploadMutation.mutate()}
                disabled={uploadMutation.isPending || !form.collection_id}
                className="bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm disabled:opacity-50"
              >
                {uploadMutation.isPending ? 'Uploading…' : 'Upload & Ingest'}
              </button>
              <button
                onClick={() => {
                  setUploadFile(null);
                  setUploadStatus('');
                }}
                className="border border-border px-4 py-2 rounded-lg text-sm"
              >
                Clear
              </button>
            </div>
          )}
          {uploadStatus && <p className="text-sm mt-2">{uploadStatus}</p>}
        </div>

        <div>
          <label className="block text-xs font-medium mb-1">Content</label>
          <textarea
            value={form.content}
            onChange={(e) => setForm((f) => ({ ...f, content: e.target.value }))}
            placeholder={
              form.source_type === 'git'
                ? 'https://github.com/org/repo'
                : form.source_type === 'openapi'
                ? 'https://api.example.com/openapi.json'
                : 'Paste your document content here…'
            }
            rows={8}
            className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary resize-none font-mono"
          />
        </div>

        {ingestMutation.isError && (
          <p className="text-xs text-red-600">{String(ingestMutation.error)}</p>
        )}
        {ingestMutation.isSuccess && (
          <div className="px-3 py-2 bg-green-50 border border-green-200 rounded-lg text-sm text-green-800">
            Ingested successfully.
            {(ingestMutation.data as { doc_count?: number })?.doc_count != null &&
              ` ${(ingestMutation.data as { doc_count: number }).doc_count} chunks indexed.`}
          </div>
        )}

        <div className="flex justify-end">
          <button
            onClick={() => ingestMutation.mutate()}
            disabled={
              !form.collection_id || !form.content.trim() || ingestMutation.isPending
            }
            className="bg-primary text-primary-foreground px-5 py-2 rounded-lg text-sm hover:opacity-90 disabled:opacity-50"
          >
            {ingestMutation.isPending ? 'Ingesting…' : 'Ingest'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Search tab ────────────────────────────────────────────────────────────────

function SearchTab({
  apiKey,
  collections,
}: {
  apiKey: string;
  collections: Collection[];
}) {
  const [query, setQuery] = useState('');
  const [collectionId, setCollectionId] = useState('');
  const [topK, setTopK] = useState(10);
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState('');

  const handleSearch = async () => {
    if (!query.trim()) return;
    setSearching(true);
    setSearchError('');
    try {
      const params = new URLSearchParams({ q: query, top_k: String(topK) });
      if (collectionId) params.set('collection_id', collectionId);
      const data = await apiFetch<SearchResult[]>(
        apiKey,
        `/knowledge/search?${params}`
      );
      setResults(data);
    } catch (e) {
      setSearchError(String(e));
    } finally {
      setSearching(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="bg-card border border-border rounded-xl p-5 space-y-3">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div className="md:col-span-2">
            <label className="block text-xs font-medium mb-1">Query</label>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              placeholder="Search your knowledge base…"
              className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
            />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1">Collection</label>
            <select
              value={collectionId}
              onChange={(e) => setCollectionId(e.target.value)}
              className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
            >
              <option value="">All collections</option>
              {collections.map((c) => (
                <option key={c.collection_id} value={c.collection_id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="flex items-center justify-between">
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            Top{' '}
            <input
              type="number"
              min="1"
              max="100"
              value={topK}
              onChange={(e) => setTopK(parseInt(e.target.value, 10) || 10)}
              className="w-14 border border-input rounded px-2 py-1 text-sm bg-background outline-none"
            />{' '}
            results
          </label>
          <button
            onClick={handleSearch}
            disabled={!query.trim() || searching}
            className="bg-primary text-primary-foreground px-5 py-2 rounded-lg text-sm hover:opacity-90 disabled:opacity-50"
          >
            {searching ? 'Searching…' : 'Search'}
          </button>
        </div>
      </div>

      {searchError && <p className="text-sm text-red-500">{searchError}</p>}

      {results !== null && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            {results.length} result{results.length !== 1 ? 's' : ''}
          </p>
          {results.length === 0 ? (
            <div className="bg-card border border-border rounded-xl py-10 text-center text-sm text-muted-foreground">
              No results found.
            </div>
          ) : (
            results.map((r, i) => (
              <div
                key={r.doc_id ?? i}
                className="bg-card border border-border rounded-xl p-4"
              >
                <div className="flex justify-between items-start mb-2">
                  <span className="text-xs text-muted-foreground font-mono">
                    {r.doc_id ?? `result-${i + 1}`}
                  </span>
                  <span className="text-xs font-medium bg-blue-100 text-blue-800 px-2 py-0.5 rounded-full">
                    score: {r.score.toFixed(4)}
                  </span>
                </div>
                <p className="text-sm leading-relaxed">{r.content}</p>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

type Tab = 'collections' | 'ingest' | 'search';
const TABS: Tab[] = ['collections', 'ingest', 'search'];

export function KnowledgePage() {
  const apiKey = useAuthStore((s) => s.apiKey);
  const [tab, setTab] = useState<Tab>('collections');

  const { data: collections = [], isLoading, error } = useQuery({
    queryKey: ['collections'],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/knowledge/collections`, {
        headers: { 'X-API-Key': apiKey },
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return res.json() as Promise<Collection[]>;
    },
    enabled: !!apiKey,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Knowledge</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Manage collections, ingest documents, and search
        </p>
      </div>

      {/* Tabs */}
      <div className="flex gap-4 border-b border-border">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`pb-2 px-1 capitalize font-medium text-sm transition-colors ${
              tab === t
                ? 'border-b-2 border-primary text-primary'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {t}
            {t === 'collections' && ` (${collections.length})`}
          </button>
        ))}
      </div>

      {tab === 'collections' && (
        <CollectionsTab
          apiKey={apiKey}
          collections={collections}
          isLoading={isLoading}
          error={error as Error | null}
        />
      )}
      {tab === 'ingest' && (
        <IngestTab apiKey={apiKey} collections={collections} />
      )}
      {tab === 'search' && (
        <SearchTab apiKey={apiKey} collections={collections} />
      )}
    </div>
  );
}
