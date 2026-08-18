/**
 * ArtifactsBrowserPage — world-class artifact browser.
 *
 * Features:
 *  - Search by name (client-side)
 *  - Type filter pills (all, file, image, screenshot, report, code)
 *  - Sort: newest, oldest, largest, smallest
 *  - 3-column card grid with type badge, size, time-ago, storage backend badge
 *  - Detail drawer: inline viewer (image/JSON/text/CSV/fallback), download, delete, "Use as Input", "Go to Goal"
 *  - 30s auto-refresh
 */
import { useState, useMemo, useCallback, useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  FileText, Image, Download, Trash2, Search, X, Copy, Check,
  ExternalLink, RefreshCw, Archive, Code, BarChart2, ArrowRight,
  Camera, AlertCircle, Loader2, LayoutList, LayoutGrid,
} from "lucide-react";
import { artifactsApi, type Artifact, type ArtifactListResponse } from "@/lib/api/client";
import { useDebounce } from "@/hooks/useDebounce";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { ConfirmModal } from "@/components/ui/ConfirmModal";
import { Pagination } from "@/components/ui/Pagination";
import { toast } from "@/stores/toast";
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';

// ── Helpers ───────────────────────────────────────────────────────────────────

type ArtifactType = "all" | "file" | "image" | "screenshot" | "report" | "code";
type SortKey = "newest" | "oldest" | "largest" | "smallest";

const TYPE_META: Record<string, { icon: React.ElementType; color: string; bg: string }> = {
  image:      { icon: Image,    color: "text-blue-600 dark:text-blue-400",   bg: "bg-blue-100 dark:bg-blue-900/30" },
  screenshot: { icon: Camera,   color: "text-violet-600 dark:text-violet-400", bg: "bg-violet-100 dark:bg-violet-900/30" },
  report:     { icon: BarChart2, color: "text-emerald-600 dark:text-emerald-400", bg: "bg-emerald-100 dark:bg-emerald-900/30" },
  code:       { icon: Code,     color: "text-amber-600 dark:text-amber-400",  bg: "bg-amber-100 dark:bg-amber-900/30" },
  file:       { icon: FileText, color: "text-slate-600 dark:text-slate-400",  bg: "bg-slate-100 dark:bg-slate-800" },
};

function typeMeta(artifactType: string) {
  return TYPE_META[artifactType?.toLowerCase()] ?? TYPE_META.file;
}

function formatBytes(n?: number): string {
  if (!n || n === 0) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

function timeAgo(iso?: string): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function storageBackend(uri?: string): string {
  if (!uri) return "unknown";
  if (uri.startsWith("s3://")) return "S3";
  if (uri.startsWith("gs://")) return "GCS";
  if (uri.startsWith("minio://") || uri.includes("minio")) return "MinIO";
  if (uri.startsWith("http")) return "HTTP";
  return "local";
}

function isDownloadable(uri?: string): boolean {
  return !!uri && (uri.startsWith("http://") || uri.startsWith("https://"));
}

// ── JSON viewer ───────────────────────────────────────────────────────────────

function JsonViewer({ uri }: { uri: string }) {
  const [content, setContent] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetch(uri)
      .then((r) => r.json())
      .then((data) => { setContent(data); setLoading(false); })
      .catch((e) => { setError(String(e)); setLoading(false); });
  }, [uri]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-32">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden="true" />
      </div>
    );
  }
  if (error) {
    return (
      <div className="p-3 text-sm text-muted-foreground">
        <p>Cannot load JSON preview</p>
        <a href={uri} target="_blank" rel="noreferrer" className="text-primary hover:underline text-xs mt-1 block">
          Open in new tab →
        </a>
      </div>
    );
  }
  return (
    <pre className="text-xs bg-muted/50 p-3 rounded overflow-auto max-h-64 font-mono whitespace-pre-wrap">
      {JSON.stringify(content, null, 2)}
    </pre>
  );
}

// ── Inline viewer ─────────────────────────────────────────────────────────────

function ArtifactViewer({ artifact }: { artifact: Artifact }) {
  const ct = artifact.content_type?.toLowerCase() ?? "";
  const uri = artifact.storage_uri;

  if (ct.startsWith("image/")) {
    return (
      <img
        src={uri}
        alt={artifact.name}
        className="w-full rounded-lg border border-border object-contain max-h-96"
        onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
      />
    );
  }
  if (ct === "application/json") {
    return <JsonViewer uri={uri ?? ""} />;
  }
  if (ct.startsWith("text/")) {
    return (
      <iframe
        src={uri}
        title={artifact.name}
        className="w-full h-64 border border-border rounded-lg bg-background"
        sandbox="allow-same-origin"
      />
    );
  }
  return (
    <div className="flex flex-col items-center justify-center py-8 text-muted-foreground text-center">
      <Archive className="h-8 w-8 mb-2 opacity-40" aria-hidden="true" />
      <p className="text-sm">Preview not available for {artifact.content_type}</p>
      <p className="text-xs mt-1 font-mono break-all opacity-60">{uri}</p>
    </div>
  );
}

// ── Detail Drawer ─────────────────────────────────────────────────────────────

function DetailDrawer({
  artifact,
  onClose,
  onDeleted,
}: {
  artifact: Artifact;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [copiedUri, setCopiedUri] = useState(false);
  const [usedAsInput, setUsedAsInput] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const meta = typeMeta(artifact.artifact_type);
  const Icon = meta.icon;

  const deleteMutation = useMutation({
    mutationFn: () => artifactsApi.delete(artifact.id),
    onSuccess: () => {
      toast({ kind: "success", message: "Artifact deleted." });
      qc.invalidateQueries({ queryKey: ["artifacts"] });
      onDeleted();
      onClose();
    },
    onError: (e) => toast({ kind: "error", message: `Delete failed: ${String(e)}` }),
  });

  const handleCopyUri = async () => {
    await navigator.clipboard.writeText(artifact.storage_uri);
    setCopiedUri(true);
    toast({ kind: "success", message: "URI copied — paste into next goal" });
    setTimeout(() => setCopiedUri(false), 2000);
  };

  const handleUseAsInput = () => {
    navigator.clipboard?.writeText(artifact.storage_uri ?? "").catch(() => {});
    toast({ kind: "success", message: "URI copied! Opening goal form…" });
    setUsedAsInput(true);
    setTimeout(() => setUsedAsInput(false), 2000);
    setTimeout(() => {
      navigate("/goals", { state: { prefillGoal: `Process the artifact at: ${artifact.storage_uri}` } });
    }, 500);
  };

  return (
    <div className="fixed inset-0 z-[300] flex justify-end">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />
      <aside className="relative bg-card border-l border-border w-full max-w-lg flex flex-col shadow-2xl overflow-hidden" aria-label={`Details for ${artifact.name}`}>
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-border shrink-0">
          <div className={`p-2 rounded-lg ${meta.bg}`}>
            <Icon className={`h-4 w-4 ${meta.color}`} aria-hidden="true" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-bold truncate" title={artifact.name}>{artifact.name}</h2>
            <div className="flex items-center gap-2 mt-0.5 flex-wrap">
              <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium capitalize ${meta.bg} ${meta.color}`}>
                {artifact.artifact_type}
              </span>
              <span className="text-[10px] text-muted-foreground">{formatBytes(artifact.size_bytes)}</span>
              <span className="text-[10px] text-muted-foreground">{timeAgo(artifact.created_at)}</span>
              <span className="text-[10px] bg-muted text-muted-foreground px-1.5 py-0.5 rounded">
                {storageBackend(artifact.storage_uri)}
              </span>
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-muted/60 text-muted-foreground" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Scrollable content */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {/* Preview */}
          <div>
            <p className="text-xs font-semibold text-muted-foreground mb-2 uppercase tracking-wide">Preview</p>
            <ArtifactViewer artifact={artifact} />
          </div>

          {/* Meta */}
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div className="bg-muted/30 rounded-lg p-3">
              <p className="text-muted-foreground mb-0.5">Content Type</p>
              <p className="font-mono font-medium">{artifact.content_type}</p>
            </div>
            <div className="bg-muted/30 rounded-lg p-3">
              <p className="text-muted-foreground mb-0.5">Size</p>
              <p className="font-medium">{formatBytes(artifact.size_bytes)}</p>
            </div>
            <div className="bg-muted/30 rounded-lg p-3">
              <p className="text-muted-foreground mb-0.5">Created</p>
              <p className="font-medium">{artifact.created_at ? new Date(artifact.created_at).toLocaleString() : "—"}</p>
            </div>
            <div className="bg-muted/30 rounded-lg p-3">
              <p className="text-muted-foreground mb-0.5">Storage</p>
              <p className="font-mono font-medium">{storageBackend(artifact.storage_uri)}</p>
            </div>
          </div>

          {/* Storage URI */}
          <div>
            <p className="text-xs font-semibold text-muted-foreground mb-1.5 uppercase tracking-wide">Storage URI</p>
            <div className="flex items-center gap-2 bg-muted/40 rounded-lg px-3 py-2">
              <p className="text-xs font-mono truncate flex-1 text-muted-foreground">{artifact.storage_uri}</p>
              <button onClick={handleCopyUri} className="shrink-0 text-muted-foreground hover:text-foreground" aria-label="Copy URI">
                {copiedUri ? <Check className="h-3.5 w-3.5 text-green-500" /> : <Copy className="h-3.5 w-3.5" />}
              </button>
            </div>
          </div>
        </div>

        {/* Actions footer */}
        <div className="shrink-0 border-t border-border px-5 py-4 bg-card space-y-2">
          <div className="grid grid-cols-2 gap-2">
            {isDownloadable(artifact.storage_uri) ? (
              <a
                href={artifact.storage_uri}
                download={artifact.name}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center justify-center gap-1.5 px-3 py-2 border border-border rounded-lg text-xs font-medium hover:bg-muted/60 transition-colors"
              >
                <Download className="h-3.5 w-3.5" aria-hidden="true" /> Download
              </a>
            ) : (
              <button disabled className="flex items-center justify-center gap-1.5 px-3 py-2 border border-border rounded-lg text-xs font-medium opacity-40 cursor-not-allowed" title="Direct download not available for this storage type">
                <Download className="h-3.5 w-3.5" aria-hidden="true" /> Download
              </button>
            )}
            <button
              onClick={handleUseAsInput}
              className="flex items-center justify-center gap-1.5 px-3 py-2 border border-border rounded-lg text-xs font-medium hover:bg-muted/60 transition-colors"
            >
              {usedAsInput ? <Check className="h-3.5 w-3.5 text-green-500" /> : <ArrowRight className="h-3.5 w-3.5" />}
              Use as Input
            </button>
            <button
              onClick={() => navigate(`/goals/${artifact.goal_id}`)}
              className="flex items-center justify-center gap-1.5 px-3 py-2 border border-border rounded-lg text-xs font-medium hover:bg-muted/60 transition-colors"
            >
              <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" /> Go to Goal
            </button>
            <button
              onClick={() => setConfirmDelete(true)}
              className="flex items-center justify-center gap-1.5 px-3 py-2 border border-destructive/50 text-destructive rounded-lg text-xs font-medium hover:bg-destructive/10 transition-colors"
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden="true" /> Delete
            </button>
          </div>
        </div>

        <ConfirmModal
          open={confirmDelete}
          title="Delete artifact?"
          description="This permanently removes the artifact metadata. The underlying file may still exist in storage."
          confirmLabel="Delete"
          variant="danger"
          isLoading={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate()}
          onCancel={() => setConfirmDelete(false)}
        />
      </aside>
    </div>
  );
}

// ── Artifact card ─────────────────────────────────────────────────────────────

function ArtifactCard({
  artifact,
  onOpen,
}: {
  artifact: Artifact;
  onOpen: () => void;
}) {
  const meta = typeMeta(artifact.artifact_type);
  const Icon = meta.icon;
  const navigate = useNavigate();

  return (
    <div
      className="bg-card border border-border rounded-xl p-4 hover:border-primary/30 hover:shadow-sm transition-[color,background-color,border-color,opacity,box-shadow,transform] cursor-pointer group flex flex-col gap-3"
      onClick={onOpen}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && onOpen()}
      data-testid="artifact-card"
      aria-label={`View artifact ${artifact.name}`}
    >
      {/* Icon + name */}
      <div className="flex items-start gap-3">
        <div className={`p-2 rounded-lg shrink-0 group-hover:opacity-80 transition-opacity ${meta.bg}`}>
          <Icon className={`h-4 w-4 ${meta.color}`} aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold truncate leading-tight" title={artifact.name}>{artifact.name}</p>
          <p className="text-[10px] text-muted-foreground mt-0.5 font-mono">{artifact.content_type}</p>
        </div>
      </div>

      {/* Badges */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium capitalize ${meta.bg} ${meta.color}`}>
          {artifact.artifact_type}
        </span>
        <span className="text-[10px] bg-muted text-muted-foreground px-1.5 py-0.5 rounded">
          {storageBackend(artifact.storage_uri)}
        </span>
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between text-[10px] text-muted-foreground mt-auto pt-2 border-t border-border">
        <span>{formatBytes(artifact.size_bytes)}</span>
        <span>{timeAgo(artifact.created_at)}</span>
      </div>

      {/* Goal link */}
      <div onClick={(e) => e.stopPropagation()}>
        <button
          onClick={() => navigate(`/goals/${artifact.goal_id}`)}
          className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-primary transition-colors font-mono"
          data-testid="goal-link"
          aria-label="Go to goal"
        >
          <ExternalLink className="h-3 w-3" aria-hidden="true" />
          {artifact.goal_id?.slice(0, 16) ?? '(no goal)'}…
        </button>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

const ARTIFACT_TYPES: ArtifactType[] = ["all", "file", "image", "screenshot", "report", "code"];
const PAGE_SIZE = 30;

export function ArtifactsBrowserPage() {
  const qc = useQueryClient();
  const [sort, setSort] = useState<SortKey>("newest");
  const [selected, setSelected] = useState<Artifact | null>(null);
  const [groupByGoal, setGroupByGoal] = useState(false);

  // ── URL-backed search/filter/page ─────────────────────────────────────────
  const [searchParams, setSearchParams] = useSearchParams();
  const page = parseInt(searchParams.get("page") ?? "1", 10);
  const typeFilter = (searchParams.get("type") ?? "all") as ArtifactType;
  const search = searchParams.get("q") ?? "";

  // Local state for immediate UI response; debounced value drives URL
  const [searchInput, setSearchInput] = useState(search);
  const debouncedSearch = useDebounce(searchInput, 400);

  const updateParams = (updates: Record<string, string | null>) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      Object.entries(updates).forEach(([k, v]) => {
        if (!v || v === "all") next.delete(k);
        else next.set(k, v);
      });
      return next;
    });
  };

  // Sync debounced search value to URL (avoids a query per keystroke)
  useEffect(() => {
    updateParams({ q: debouncedSearch || null, page: null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch]);

  const { data: rawData, isLoading, error } = useQuery<ArtifactListResponse>({
    queryKey: ["artifacts", page, typeFilter, search],
    queryFn: () => artifactsApi.list({
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
      type: typeFilter !== "all" ? typeFilter : undefined,
      search: search || undefined,
    }),
    refetchInterval: 30_000,
    staleTime: 15_000,
  });

  // Normalise response — backend may return bare array or paginated object
  const artifacts: Artifact[] = Array.isArray(rawData)
    ? rawData
    : (rawData as { items: Artifact[]; total: number } | undefined)?.items ?? [];
  const total: number = Array.isArray(rawData)
    ? rawData.length
    : (rawData as { items: Artifact[]; total: number } | undefined)?.total ?? 0;

  // Client-side sort for the current page (server handles filter/search/offset)
  const filtered = useMemo(() => {
    const normalizedSearch = search.toLowerCase();
    const list = artifacts.filter((artifact) => {
      const matchesType = typeFilter === "all" || artifact.artifact_type === typeFilter;
      const searchable = `${artifact.name ?? ""} ${artifact.content_type ?? ""}`.toLowerCase();
      return matchesType && (!normalizedSearch || searchable.includes(normalizedSearch));
    });
    switch (sort) {
      case "newest": list.sort((a, b) => new Date(b.created_at ?? 0).getTime() - new Date(a.created_at ?? 0).getTime()); break;
      case "oldest": list.sort((a, b) => new Date(a.created_at ?? 0).getTime() - new Date(b.created_at ?? 0).getTime()); break;
      case "largest": list.sort((a, b) => (b.size_bytes ?? 0) - (a.size_bytes ?? 0)); break;
      case "smallest": list.sort((a, b) => (a.size_bytes ?? 0) - (b.size_bytes ?? 0)); break;
    }
    return list;
  }, [artifacts, search, sort, typeFilter]);

  const grouped = useMemo(() => {
    if (!groupByGoal) return null;
    const groups = new Map<string, typeof filtered>();
    filtered.forEach((a) => {
      const key = a.goal_id ?? "__no_goal__";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(a);
    });
    return groups;
  }, [filtered, groupByGoal]);

  const handleRefresh = useCallback(() => qc.invalidateQueries({ queryKey: ["artifacts"] }), [qc]);

  return (
    <JARVISPageShell>
    <div className="space-y-6 max-w-6xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Archive className="h-6 w-6 text-primary" aria-hidden="true" />
            Artifacts
            {total > 0 && (
              <span className="text-sm font-normal text-muted-foreground ml-1">({total})</span>
            )}
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">Files and outputs produced by agent runs</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setGroupByGoal((v) => !v)}
            className={`p-2 rounded-lg transition-colors ${groupByGoal ? "bg-primary/10 text-primary" : "hover:bg-muted/60 text-muted-foreground hover:text-foreground"}`}
            aria-label={groupByGoal ? "Switch to flat list" : "Group by goal"}
            title={groupByGoal ? "Flat list" : "Group by goal"}
          >
            {groupByGoal ? <LayoutGrid className="h-4 w-4" aria-hidden="true" /> : <LayoutList className="h-4 w-4" aria-hidden="true" />}
          </button>
          <button
            onClick={handleRefresh}
            className="p-2 rounded-lg hover:bg-muted/60 text-muted-foreground hover:text-foreground transition-colors"
            aria-label="Refresh artifacts"
            title="Refresh"
          >
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3">
        {/* Search */}
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" aria-hidden="true" />
          <input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search by name or type…"
            aria-label="Search artifacts"
            className="w-full pl-9 pr-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary"
          />
        </div>
        {/* Type pills */}
        <div className="flex gap-1.5 flex-wrap">
          {ARTIFACT_TYPES.map((t) => (
            <button
              key={t}
              onClick={() => updateParams({ type: t, page: null })}
              className={`px-3 py-1.5 text-xs rounded-lg border capitalize transition-colors ${
                typeFilter === t ? "bg-primary text-primary-foreground border-primary" : "border-border hover:bg-muted/50 text-muted-foreground"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
        {/* Sort */}
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value as SortKey)}
          className="text-xs border border-input rounded-lg px-2 py-1.5 bg-background focus:outline-none focus:ring-1 focus:ring-primary"
          aria-label="Sort artifacts"
        >
          <option value="newest">Newest first</option>
          <option value="oldest">Oldest first</option>
          <option value="largest">Largest first</option>
          <option value="smallest">Smallest first</option>
        </select>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl px-4 py-3 text-sm text-red-700 dark:text-red-400">
          <AlertCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
          Failed to load artifacts: {String(error)}
        </div>
      )}

      {/* Loading */}
      {isLoading && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-40 rounded-xl" />)}
        </div>
      )}

      {/* Empty */}
      {!isLoading && !error && filtered.length === 0 && (
        <EmptyState
          title={search || typeFilter !== "all" ? "No matching artifacts" : "No artifacts yet"}
          description={search || typeFilter !== "all" ? "Try adjusting your search or filter." : "Artifacts are created when agents produce files during goal execution."}
        />
      )}

      {/* Results count */}
      {!isLoading && total > 0 && (
        <p className="text-xs text-muted-foreground">
          {total} artifact{total !== 1 ? "s" : ""}
          {typeFilter !== "all" && ` of type "${typeFilter}"`}
          {search && ` matching "${search}"`}
        </p>
      )}

      {/* Grid / Grouped */}
      {!isLoading && filtered.length > 0 && (
        groupByGoal && grouped ? (
          <div className="space-y-6">
            {Array.from(grouped.entries()).map(([goalId, goalArtifacts]) => (
              <div key={goalId}>
                <h3 className="text-xs font-semibold text-muted-foreground mb-2 font-mono flex items-center gap-1.5">
                  <ExternalLink className="h-3 w-3" aria-hidden="true" />
                  {goalId === "__no_goal__" ? "(No goal)" : `Goal: ${goalId.slice(0, 16)}…`}
                  <span className="font-normal">({goalArtifacts.length})</span>
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  {goalArtifacts.map((a) => (
                    <ArtifactCard key={a.id} artifact={a} onOpen={() => setSelected(a)} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {filtered.map((a) => (
              <ArtifactCard key={a.id} artifact={a} onOpen={() => setSelected(a)} />
            ))}
          </div>
        )
      )}

      {/* Detail drawer */}
      {selected && (
        <DetailDrawer
          artifact={selected}
          onClose={() => setSelected(null)}
          onDeleted={() => setSelected(null)}
        />
      )}

      {/* Pagination */}
      {!isLoading && total > PAGE_SIZE && (
        <Pagination
          page={page}
          pageSize={PAGE_SIZE}
          total={total}
          onPageChange={(p) => updateParams({ page: String(p) })}
          onPageSizeChange={() => {}}
        />
      )}
    </div>
    </JARVISPageShell>
  );
}
