/**
 * TrainingExportPage — World-Class Fine-Tuning Data Export
 *
 * Features:
 *   - Live preview panel (count + score distribution) before downloading
 *   - Sample records in the chosen format (OpenAI / Anthropic)
 *   - Train / Validation split with configurable ratio
 *   - Export history (localStorage, last 10)
 *   - One-click export to JSONL
 */
import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  BarChart2,
  BookOpen,
  ChevronRight,
  ClipboardCopy,
  Download,
  ExternalLink,
  FileJson,
  GraduationCap,
  History,
  Loader2,
  RefreshCw,
  Scissors,
  Sparkles,
  Trash2,
} from 'lucide-react';
import { trainingApi, type TrainingPreview } from '@/lib/api/client';
import { toast } from '@/stores/toast';

// ── Types ──────────────────────────────────────────────────────────────────────

type Format = 'openai' | 'anthropic';

interface ExportRecord {
  id: string;
  format: Format;
  minScore: number;
  limit: number;
  count: number;
  filename: string;
  timestamp: number;
  splitRatio?: number;
}

// ── localStorage helpers ───────────────────────────────────────────────────────

const HISTORY_KEY = 'training_export_history_v1';

function loadHistory(): ExportRecord[] {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) ?? '[]'); }
  catch { return []; }
}

function addToExportHistory(record: Omit<ExportRecord, 'id' | 'timestamp'>): void {
  const history = loadHistory();
  history.unshift({ ...record, id: crypto.randomUUID(), timestamp: Date.now() });
  localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(0, 10)));
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function formatDate(ts: number): string {
  return new Date(ts).toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

// ── Score Distribution Chart ──────────────────────────────────────────────────

function ScoreDistribution({ data }: { data: Record<string, number> }) {
  const max = Math.max(...Object.values(data), 1);
  const colors: Record<string, string> = {
    '0.80-0.85': 'bg-amber-400',
    '0.85-0.90': 'bg-yellow-400',
    '0.90-0.95': 'bg-lime-400',
    '0.95-1.00': 'bg-green-500',
  };
  return (
    <div data-testid="score-distribution" className="space-y-1.5">
      {Object.entries(data).map(([bucket, count]) => (
        <div key={bucket} className="flex items-center gap-2 text-xs">
          <span className="w-20 text-muted-foreground shrink-0">{bucket}</span>
          <div className="flex-1 bg-muted rounded h-4 overflow-hidden">
            <div
              className={`h-full rounded transition-all ${colors[bucket] ?? 'bg-primary'}`}
              style={{ width: `${Math.max(4, (count / max) * 100)}%` }}
            />
          </div>
          <span className="w-8 text-right font-mono">{count}</span>
        </div>
      ))}
    </div>
  );
}

// ── Sample Record Preview ─────────────────────────────────────────────────────

function SampleRecords({ samples, format }: { samples: TrainingPreview['samples']; format: Format }) {
  if (!samples.length) return null;
  return (
    <div className="space-y-2">
      {samples.slice(0, 2).map((s, i) => (
        <div key={i} className="bg-muted/50 rounded-lg p-3 border border-border text-xs font-mono">
          <div className="flex items-center justify-between mb-1.5 font-sans">
            <span className="font-medium text-foreground/80 not-italic text-xs">
              Example {i + 1}
            </span>
            <span className={`px-1.5 py-0.5 rounded text-xs font-sans ${
              s.eval_score >= 0.95 ? 'bg-green-100 text-green-700' :
              s.eval_score >= 0.9  ? 'bg-lime-100 text-lime-700' :
              'bg-amber-100 text-amber-700'
            }`}>
              score {s.eval_score.toFixed(2)}
            </span>
          </div>
          {format === 'openai' ? (
            <pre className="whitespace-pre-wrap text-[10px] leading-relaxed text-foreground/70 overflow-auto max-h-32">
{`{ "messages": [
  { "role": "system", "content": "You are an autonomous AI agent." },
  { "role": "user",   "content": ${JSON.stringify(s.goal)} },
  ... (${s.steps} steps, tools: [${s.tools.slice(0,3).map(t => `"${t}"`).join(', ')}${s.tools.length > 3 ? '…' : ''}])
  { "role": "assistant", "content": "<result>" }
], "metadata": { "eval_score": ${s.eval_score} } }`}
            </pre>
          ) : (
            <pre className="whitespace-pre-wrap text-[10px] leading-relaxed text-foreground/70 overflow-auto max-h-32">
{`{ "system": "You are an autonomous AI agent.",
  "messages": [
    { "role": "user",      "content": ${JSON.stringify(s.goal)} },
    ... (${s.steps} steps, tools: [${s.tools.slice(0,3).map(t => `"${t}"`).join(', ')}${s.tools.length > 3 ? '…' : ''}])
    { "role": "assistant", "content": "<result>" }
  ], "metadata": { "eval_score": ${s.eval_score} } }`}
            </pre>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Stat Card ─────────────────────────────────────────────────────────────────

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="bg-card border border-border rounded-lg p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-xl font-bold mt-0.5">{value}</p>
      {sub && <p className="text-xs text-muted-foreground mt-0.5">{sub}</p>}
    </div>
  );
}

// ── Export History Panel ──────────────────────────────────────────────────────

function ExportHistoryPanel({ refresh }: { refresh: number }) {
  const [history, setHistory] = useState<ExportRecord[]>(() => loadHistory());

  const staleRefresh = refresh; // silence unused
  void staleRefresh;

  const handleClear = () => {
    localStorage.removeItem(HISTORY_KEY);
    setHistory([]);
  };

  if (!history.length) return null;

  return (
    <div className="bg-card border border-border rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-muted/40">
        <span className="font-medium text-sm flex items-center gap-2">
          <History className="h-4 w-4" /> Export History
        </span>
        <button
          onClick={handleClear}
          className="text-xs text-muted-foreground hover:text-destructive flex items-center gap-1"
        >
          <Trash2 className="h-3.5 w-3.5" /> Clear
        </button>
      </div>
      <div className="divide-y divide-border">
        {history.map((r) => (
          <div key={r.id} className="px-4 py-2.5 flex items-center gap-4 text-sm">
            <span
              className={`px-2 py-0.5 rounded text-xs font-medium uppercase ${
                r.format === 'openai'
                  ? 'bg-green-100 text-green-700'
                  : 'bg-orange-100 text-orange-700'
              }`}
            >
              {r.format}
            </span>
            <div className="flex-1 min-w-0">
              <p className="font-mono text-xs truncate text-muted-foreground">{r.filename}</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                {r.count} examples · score ≥ {r.minScore.toFixed(2)}
                {r.splitRatio !== undefined && ` · split ${Math.round(r.splitRatio * 100)}/${Math.round((1 - r.splitRatio) * 100)}`}
              </p>
            </div>
            <span className="text-xs text-muted-foreground whitespace-nowrap shrink-0">
              {formatDate(r.timestamp)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export function TrainingExportPage() {
  const [format, setFormat] = useState<Format>('openai');
  const [minScore, setMinScore] = useState(0.8);
  const [limit, setLimit] = useState(1000);
  const [splitEnabled, setSplitEnabled] = useState(false);
  const [splitRatio, setSplitRatio] = useState(0.8); // train fraction
  const [lastCount, setLastCount] = useState<number | null>(null);
  const [historyRefresh, setHistoryRefresh] = useState(0);
  const [showHistory, setShowHistory] = useState(false);

  // Live preview query (fires when params change)
  const {
    data: preview,
    isFetching: previewLoading,
    refetch: refetchPreview,
  } = useQuery({
    queryKey: ['training-preview', minScore, limit],
    queryFn: () => trainingApi.preview({ minScore, limit }),
    staleTime: 15_000,
    enabled: true,
  });

  const exportMutation = useMutation({
    mutationFn: () => trainingApi.export({ format, minScore, limit }),
    onSuccess: ({ blob, filename, count }) => {
      setLastCount(count);
      if (count === 0) {
        toast({ kind: 'info', message: 'No examples matched the filters.' });
        return;
      }
      if (splitEnabled && count >= 2) {
        // Split into train / validation blobs
        splitAndDownload(blob, filename, splitRatio);
      } else {
        triggerDownload(blob, filename);
      }
      toast({ kind: 'success', message: `Exported ${count} training examples as ${format.toUpperCase()} JSONL.` });
      addToExportHistory({ format, minScore, limit, count, filename, splitRatio: splitEnabled ? splitRatio : undefined });
      setHistoryRefresh((n) => n + 1);
      setShowHistory(true);
    },
    onError: () => toast({ kind: 'error', message: 'Export failed. Check server logs.' }),
  });

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <GraduationCap className="h-6 w-6 text-indigo-500" />
            Training-Data Export
          </h1>
          <p className="text-muted-foreground text-sm mt-1">
            Export high-scoring agent goal runs as JSONL for LLM fine-tuning
          </p>
        </div>
        <a
          href="https://platform.openai.com/docs/guides/fine-tuning"
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-1.5 text-xs text-blue-500 hover:underline"
        >
          <ExternalLink className="h-3.5 w-3.5" />
          OpenAI fine-tuning docs
        </a>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-5">
        {/* ── Left: Filters (2 cols) ──────────────────────── */}
        <div className="lg:col-span-2 space-y-4">
          <div className="bg-card border border-border rounded-xl p-4 space-y-4">
            <h2 className="font-semibold text-sm flex items-center gap-2">
              <FileJson className="h-4 w-4" /> Export Filters
            </h2>

            {/* Format */}
            <div>
              <label htmlFor="format" className="block text-sm font-medium mb-1">
                Format
              </label>
              <select
                id="format"
                value={format}
                onChange={(e) => setFormat(e.target.value as Format)}
                className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background"
              >
                <option value="openai">OpenAI (chat completion fine-tuning)</option>
                <option value="anthropic">Anthropic (Claude fine-tuning)</option>
              </select>
              <p className="text-xs text-muted-foreground mt-1">
                {format === 'openai'
                  ? 'Produces {"messages": [...]} per line — compatible with gpt-3.5-turbo fine-tuning.'
                  : 'Produces {"system": ..., "messages": [...]} per line — compatible with Claude fine-tuning.'}
              </p>
            </div>

            {/* Min eval score */}
            <div>
              <label htmlFor="min-score" className="block text-sm font-medium mb-1">
                Minimum eval score:{' '}
                <span className="font-mono text-primary">{minScore.toFixed(2)}</span>
              </label>
              <input
                id="min-score"
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={minScore}
                onChange={(e) => setMinScore(Number(e.target.value))}
                className="w-full accent-primary"
              />
              <div className="flex justify-between text-xs text-muted-foreground mt-0.5">
                <span>0.00 (all)</span>
                <span>1.00 (perfect)</span>
              </div>
            </div>

            {/* Max examples */}
            <div>
              <label htmlFor="limit" className="block text-sm font-medium mb-1">
                Max examples
              </label>
              <input
                id="limit"
                type="number"
                min={1}
                max={10000}
                value={limit}
                onChange={(e) => setLimit(Math.max(1, Number(e.target.value)))}
                className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background"
              />
            </div>

            {/* Train / val split */}
            <div className="border border-border rounded-lg p-3 space-y-2">
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  id="split-toggle"
                  checked={splitEnabled}
                  onChange={(e) => setSplitEnabled(e.target.checked)}
                  className="rounded"
                />
                <Scissors className="h-4 w-4" />
                <span>Train / Validation split</span>
              </label>
              {splitEnabled && (
                <div className="pl-6 space-y-1">
                  <p className="text-xs text-muted-foreground">
                    Train {Math.round(splitRatio * 100)}% · Val {Math.round((1 - splitRatio) * 100)}%
                  </p>
                  <input
                    type="range"
                    id="split-ratio"
                    min={0.5}
                    max={0.95}
                    step={0.05}
                    value={splitRatio}
                    onChange={(e) => setSplitRatio(Number(e.target.value))}
                    className="w-full accent-primary"
                    aria-label="Train/Val split ratio"
                  />
                  <p className="text-xs text-muted-foreground">
                    Downloads two files: <code className="font-mono">…_train.jsonl</code> and{' '}
                    <code className="font-mono">…_val.jsonl</code>
                  </p>
                </div>
              )}
            </div>

            {/* Refresh preview */}
            <button
              onClick={() => void refetchPreview()}
              disabled={previewLoading}
              className="w-full flex items-center justify-center gap-2 px-3 py-2 border border-border rounded-md text-sm hover:bg-muted disabled:opacity-50"
            >
              {previewLoading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
              Refresh preview
            </button>
          </div>
        </div>

        {/* ── Right: Preview + Export (3 cols) ────────────── */}
        <div className="lg:col-span-3 space-y-4">
          {/* Stats cards */}
          {preview && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <StatCard
                label="Matching examples"
                value={preview.count.toLocaleString()}
                sub={`of ${limit.toLocaleString()} max`}
              />
              <StatCard
                label="Avg eval score"
                value={preview.avg_score.toFixed(3)}
              />
              <StatCard
                label="Score range"
                value={`${preview.min_score_found.toFixed(2)}–${preview.max_score_found.toFixed(2)}`}
              />
              <StatCard
                label="Split size"
                value={splitEnabled ? `${Math.round(preview.count * splitRatio)}+${Math.round(preview.count * (1 - splitRatio))}` : '—'}
                sub={splitEnabled ? 'train+val' : 'split disabled'}
              />
            </div>
          )}
          {previewLoading && !preview && (
            <div className="flex items-center gap-2 text-muted-foreground text-sm p-4">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading preview…
            </div>
          )}

          {/* Score distribution */}
          {preview && (
            <div className="bg-card border border-border rounded-xl p-4 space-y-3">
              <h3 className="font-medium text-sm flex items-center gap-2">
                <BarChart2 className="h-4 w-4" /> Score Distribution
              </h3>
              <ScoreDistribution data={preview.score_distribution} />
            </div>
          )}

          {/* Sample records */}
          {preview && preview.samples.length > 0 && (
            <div className="bg-card border border-border rounded-xl p-4 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="font-medium text-sm flex items-center gap-2">
                  <BookOpen className="h-4 w-4" /> Sample Records
                </h3>
                <span className="text-xs text-muted-foreground">
                  Format: {format === 'openai' ? 'OpenAI chat' : 'Anthropic'}
                </span>
              </div>
              <SampleRecords samples={preview.samples} format={format} />
            </div>
          )}

          {/* Export button */}
          <div className="bg-card border border-border rounded-xl p-4 space-y-3">
            <div className="flex items-start justify-between gap-3 flex-wrap">
              <div>
                <h3 className="font-medium text-sm flex items-center gap-2">
                  <Sparkles className="h-4 w-4 text-indigo-500" /> Export
                </h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {preview
                    ? `${preview.count} examples ready · ${format.toUpperCase()} format${splitEnabled ? ` · ${Math.round(splitRatio * 100)}/${Math.round((1 - splitRatio) * 100)} split` : ''}`
                    : 'Preview to confirm count before exporting'}
                </p>
              </div>
              <button
                data-testid="btn-export"
                onClick={() => exportMutation.mutate()}
                disabled={exportMutation.isPending}
                className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50 hover:bg-primary/90 transition-colors"
              >
                {exportMutation.isPending ? (
                  <><Loader2 className="h-4 w-4 animate-spin" /> Exporting…</>
                ) : (
                  <><Download className="h-4 w-4" /> Export JSONL</>
                )}
              </button>
            </div>

            {lastCount != null && (
              <div
                data-testid="export-result"
                className={`text-sm flex items-center gap-2 p-2 rounded-md ${
                  lastCount === 0
                    ? 'bg-amber-50 text-amber-700 border border-amber-200'
                    : 'bg-green-50 text-green-700 border border-green-200'
                }`}
              >
                {lastCount === 0 ? (
                  'No examples matched the current filters.'
                ) : (
                  <><ChevronRight className="h-4 w-4" /> {lastCount} examples exported successfully.</>
                )}
              </div>
            )}
          </div>

          {/* History toggle */}
          <button
            onClick={() => setShowHistory((v) => !v)}
            className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
          >
            <History className="h-4 w-4" />
            {showHistory ? 'Hide' : 'Show'} export history
            {loadHistory().length > 0 && (
              <span className="bg-muted text-muted-foreground text-xs px-1.5 rounded-full">
                {loadHistory().length}
              </span>
            )}
          </button>
          {showHistory && <ExportHistoryPanel refresh={historyRefresh} />}
        </div>
      </div>
    </div>
  );
}

// ── Split helper (not exported, used above) ───────────────────────────────────

function splitAndDownload(blob: Blob, filename: string, trainFraction: number): void {
  const reader = new FileReader();
  reader.onload = () => {
    const lines = (reader.result as string).split('\n').filter(Boolean);
    const trainSize = Math.max(1, Math.round(lines.length * trainFraction));
    const trainLines = lines.slice(0, trainSize);
    const valLines = lines.slice(trainSize);

    const base = filename.replace(/\.jsonl$/, '');
    triggerDownload(new Blob([trainLines.join('\n')], { type: 'application/x-ndjson' }), `${base}_train.jsonl`);
    if (valLines.length > 0) {
      setTimeout(() => {
        triggerDownload(new Blob([valLines.join('\n')], { type: 'application/x-ndjson' }), `${base}_val.jsonl`);
      }, 300);
    }
  };
  reader.readAsText(blob);
}
