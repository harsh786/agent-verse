/**
 * PerceptionPage — World-Class Web Perception Lab
 *
 * Tabs:
 *   Single  — screenshot + vision analysis + text extraction for one URL
 *   Batch   — concurrent analysis of up to 10 URLs
 *   History — last 10 session analyses (localStorage)
 */
import { useCallback, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  Camera,
  CheckCircle,
  ChevronDown,
  ChevronRight,
  ClipboardCopy,
  Download,
  Eye,
  Globe,
  History,
  Loader2,
  ScanText,
  Send,
  Sparkles,
  Trash2,
  XCircle,
  ZoomIn,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { perceptionApi } from '@/lib/api/client';
import { toast } from '@/stores/toast';
import { StatusBadge } from '@/components/ui/StatusBadge';

// ── Types ──────────────────────────────────────────────────────────────────────

interface HistoryEntry {
  id: string;
  url: string;
  question: string;
  screenshot_b64: string | null;
  analysis: string | null;
  extracted: { text: string; charCount: number; selector: string } | null;
  timestamp: number;
}

type Tab = 'single' | 'batch' | 'history';

// ── Presets ───────────────────────────────────────────────────────────────────

const QUESTION_PRESETS = [
  'What is the main purpose and content of this page?',
  'What are the key UI elements and their functions?',
  'Summarize the main call-to-action on this page.',
  'What forms or inputs exist on this page?',
  'Describe any error messages or warnings shown.',
  'What navigation options are available?',
];

// ── localStorage helpers ───────────────────────────────────────────────────────

const HISTORY_KEY = 'perception_history_v1';

function loadHistory(): HistoryEntry[] {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY) ?? '[]');
  } catch {
    return [];
  }
}

function saveHistory(entries: HistoryEntry[]): void {
  localStorage.setItem(HISTORY_KEY, JSON.stringify(entries.slice(0, 10)));
}

function addToHistory(entry: Omit<HistoryEntry, 'id' | 'timestamp'>): void {
  const entries = loadHistory();
  entries.unshift({ ...entry, id: crypto.randomUUID(), timestamp: Date.now() });
  saveHistory(entries);
}

// ── Status Panel ──────────────────────────────────────────────────────────────

function StatusPanel({ playwrightOk, visionOk }: { playwrightOk: boolean; visionOk: boolean }) {
  return (
    <div
      data-testid="status-panel"
      className="bg-card border border-border rounded-xl p-3 flex flex-wrap gap-6 items-center"
    >
      <div className="flex items-center gap-2">
        <span className="text-sm text-muted-foreground">Browser (Playwright)</span>
        <StatusBadge status={playwrightOk ? 'success' : 'failed'} />
      </div>
      <div className="flex items-center gap-2">
        <span className="text-sm text-muted-foreground">Vision LLM</span>
        <StatusBadge status={visionOk ? 'success' : 'failed'} />
      </div>
      {!playwrightOk && (
        <p className="text-xs text-amber-600 flex items-center gap-1">
          <AlertTriangle className="h-3.5 w-3.5" />
          Playwright not installed on server. Run:{' '}
          <code className="font-mono bg-muted px-1 rounded">pip install playwright &amp;&amp; playwright install</code>
        </p>
      )}
    </div>
  );
}

// ── Copy button ───────────────────────────────────────────────────────────────

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => {
        void navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
      className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
      title="Copy to clipboard"
    >
      {copied ? <CheckCircle className="h-3.5 w-3.5 text-green-500" /> : <ClipboardCopy className="h-3.5 w-3.5" />}
      {copied ? 'Copied' : 'Copy'}
    </button>
  );
}

// ── Screenshot viewer ─────────────────────────────────────────────────────────

function ScreenshotCard({
  b64,
  url,
  onDownload,
}: {
  b64: string;
  url: string;
  onDownload: () => void;
}) {
  const [zoomed, setZoomed] = useState(false);
  return (
    <div className="bg-card border border-border rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 border-b border-border bg-muted/40">
        <span className="font-medium text-sm flex items-center gap-1.5">
          <Camera className="h-4 w-4" /> Screenshot
        </span>
        <div className="flex gap-2">
          <button
            onClick={() => setZoomed((z) => !z)}
            className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1"
          >
            <ZoomIn className="h-3.5 w-3.5" />
            {zoomed ? 'Fit' : 'Full size'}
          </button>
          <button
            onClick={onDownload}
            className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1"
          >
            <Download className="h-3.5 w-3.5" /> Save
          </button>
        </div>
      </div>
      <div className={`overflow-auto p-2 ${zoomed ? '' : 'max-h-80'}`}>
        <img
          src={`data:image/png;base64,${b64}`}
          alt="Screenshot"
          className={`rounded border border-border ${zoomed ? 'w-auto' : 'max-w-full'}`}
        />
      </div>
      <div className="px-4 py-1.5 border-t border-border bg-muted/30">
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          className="text-xs text-blue-500 hover:underline truncate block max-w-xs"
        >
          {url}
        </a>
      </div>
    </div>
  );
}

// ── Single Analysis Tab ───────────────────────────────────────────────────────

function SingleTab({
  disabled,
  onHistoryUpdate,
}: {
  disabled: boolean;
  onHistoryUpdate: () => void;
}) {
  const navigate = useNavigate();
  const [url, setUrl] = useState('');
  const [question, setQuestion] = useState(QUESTION_PRESETS[0]);
  const [fullPage, setFullPage] = useState(false);
  const [selector, setSelector] = useState('body');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [screenshot, setScreenshot] = useState<string | null>(null);
  const [screenshotUrl, setScreenshotUrl] = useState('');
  const [analysis, setAnalysis] = useState<string | null>(null);
  const [extracted, setExtracted] = useState<{ text: string; charCount: number; selector: string } | null>(null);

  const urlValid = url.trim().startsWith('http://') || url.trim().startsWith('https://');

  const screenshotMutation = useMutation({
    mutationFn: () => perceptionApi.screenshot(url, fullPage),
    onSuccess: (r) => {
      if (!r.success) {
        toast({ kind: 'error', message: r.error ?? 'Screenshot failed.' });
        return;
      }
      setScreenshot(r.screenshot_b64);
      setScreenshotUrl(url);
    },
  });

  const analyzeMutation = useMutation({
    mutationFn: () =>
      perceptionApi.analyze(
        screenshot ? { screenshot_b64: screenshot, question } : { url, question }
      ),
    onSuccess: (r) => {
      setAnalysis(r.analysis);
      addToHistory({
        url: screenshotUrl || url,
        question,
        screenshot_b64: screenshot,
        analysis: r.analysis,
        extracted: null,
      });
      onHistoryUpdate();
    },
  });

  const extractMutation = useMutation({
    mutationFn: () => perceptionApi.extract(url, selector),
    onSuccess: (r) => {
      if (!r.success) {
        toast({ kind: 'error', message: r.error ?? 'Extraction failed.' });
        return;
      }
      setExtracted({ text: r.text, charCount: r.char_count, selector: r.selector });
      addToHistory({
        url,
        question,
        screenshot_b64: screenshot,
        analysis,
        extracted: { text: r.text, charCount: r.char_count, selector: r.selector },
      });
      onHistoryUpdate();
    },
  });

  // Screenshot + Analyze in one shot
  const analyzePageMutation = useMutation({
    mutationFn: async () => {
      const ssResult = await perceptionApi.screenshot(url, fullPage);
      if (!ssResult.success) throw new Error(ssResult.error ?? 'Screenshot failed');
      setScreenshot(ssResult.screenshot_b64);
      setScreenshotUrl(url);
      const analysis = await perceptionApi.analyze({
        screenshot_b64: ssResult.screenshot_b64,
        question,
      });
      return analysis;
    },
    onSuccess: (r) => {
      setAnalysis(r.analysis);
      addToHistory({ url, question, screenshot_b64: screenshot, analysis: r.analysis, extracted: null });
      onHistoryUpdate();
    },
    onError: (e: Error) => toast({ kind: 'error', message: e.message }),
  });

  const handleGoalCreate = () => {
    const ctx = [analysis ? `Analysis: ${analysis}` : '', extracted ? `Extracted:\n${extracted.text.slice(0, 500)}` : ''].filter(Boolean).join('\n');
    navigate(`/goals?prefill=${encodeURIComponent(`Analyze the page at ${url}.\n${ctx}`)}`);
  };

  const handleDownloadScreenshot = () => {
    if (!screenshot) return;
    const a = document.createElement('a');
    a.href = `data:image/png;base64,${screenshot}`;
    a.download = `screenshot_${Date.now()}.png`;
    a.click();
  };

  const handleDownloadAnalysis = () => {
    if (!analysis) return;
    const blob = new Blob([`# Analysis of ${screenshotUrl || url}\n\n${analysis}`], { type: 'text/markdown' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `analysis_${Date.now()}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const isLoading =
    screenshotMutation.isPending || analyzeMutation.isPending || extractMutation.isPending || analyzePageMutation.isPending;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
      {/* ── Left: Controls ─────────────────────────────────── */}
      <div className="space-y-4">
        {/* URL input */}
        <div>
          <label htmlFor="url" className="block text-sm font-medium mb-1">
            URL
          </label>
          <input
            id="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com"
            className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background focus:ring-2 focus:ring-primary/30 focus:outline-none"
          />
          {url && !urlValid && (
            <p className="text-xs text-destructive mt-1">URL must start with http:// or https://</p>
          )}
        </div>

        {/* Question */}
        <div>
          <label htmlFor="question" className="block text-sm font-medium mb-1">
            Analysis question
          </label>
          <div className="flex gap-2">
            <input
              id="question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              className="flex-1 px-3 py-2 border border-border rounded-md text-sm bg-background focus:ring-2 focus:ring-primary/30 focus:outline-none"
            />
            <select
              aria-label="Question presets"
              onChange={(e) => e.target.value && setQuestion(e.target.value)}
              className="px-2 py-2 border border-border rounded-md text-xs bg-background"
              value=""
            >
              <option value="">Presets</option>
              {QUESTION_PRESETS.map((q) => (
                <option key={q} value={q}>{q.slice(0, 50)}…</option>
              ))}
            </select>
          </div>
        </div>

        {/* Advanced options */}
        <div>
          <button
            onClick={() => setShowAdvanced((v) => !v)}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            {showAdvanced ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
            Advanced options
          </button>
          {showAdvanced && (
            <div className="mt-3 space-y-3 pl-4 border-l-2 border-border">
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  checked={fullPage}
                  onChange={(e) => setFullPage(e.target.checked)}
                  className="rounded"
                  id="full-page-toggle"
                />
                <span>Full-page screenshot</span>
              </label>
              <div>
                <label htmlFor="selector" className="block text-xs text-muted-foreground mb-1">
                  CSS selector (for text extraction)
                </label>
                <input
                  id="selector"
                  value={selector}
                  onChange={(e) => setSelector(e.target.value)}
                  placeholder="body"
                  className="w-full px-3 py-1.5 border border-border rounded-md text-sm bg-background"
                />
              </div>
            </div>
          )}
        </div>

        {/* Action buttons */}
        <div className="flex flex-wrap gap-2">
          <button
            data-testid="btn-screenshot"
            onClick={() => screenshotMutation.mutate()}
            disabled={!urlValid || disabled || screenshotMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50 hover:bg-primary/90 transition-colors"
          >
            {screenshotMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Camera className="h-4 w-4" />}
            Screenshot
          </button>
          <button
            data-testid="btn-analyze"
            onClick={() => (screenshot ? analyzeMutation.mutate() : analyzePageMutation.mutate())}
            disabled={!urlValid || disabled || isLoading}
            className="flex items-center gap-1.5 px-3 py-2 border border-border rounded-md text-sm disabled:opacity-50 hover:bg-muted transition-colors"
          >
            {analyzeMutation.isPending || analyzePageMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Sparkles className="h-4 w-4" />
            )}
            Analyze
          </button>
          <button
            data-testid="btn-extract"
            onClick={() => extractMutation.mutate()}
            disabled={!urlValid || disabled || extractMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-2 border border-border rounded-md text-sm disabled:opacity-50 hover:bg-muted transition-colors"
          >
            {extractMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <ScanText className="h-4 w-4" />}
            Extract text
          </button>
        </div>

        {(analysis || extracted) && (
          <div className="flex gap-2 flex-wrap">
            <button
              onClick={handleGoalCreate}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 text-white rounded-md text-xs hover:bg-indigo-700"
            >
              <Send className="h-3.5 w-3.5" /> Create Goal from this page
            </button>
            {analysis && (
              <button
                onClick={handleDownloadAnalysis}
                className="flex items-center gap-1.5 px-3 py-1.5 border border-border rounded-md text-xs hover:bg-muted"
              >
                <Download className="h-3.5 w-3.5" /> Export analysis
              </button>
            )}
          </div>
        )}
      </div>

      {/* ── Right: Results ──────────────────────────────────── */}
      <div className="space-y-4">
        {screenshot && (
          <ScreenshotCard
            b64={screenshot}
            url={screenshotUrl}
            onDownload={handleDownloadScreenshot}
          />
        )}

        {analysis && (
          <div className="bg-card border border-border rounded-xl overflow-hidden">
            <div className="flex items-center justify-between px-4 py-2 border-b border-border bg-muted/40">
              <span className="font-medium text-sm flex items-center gap-1.5">
                <Sparkles className="h-4 w-4 text-violet-500" /> Vision Analysis
              </span>
              <div className="flex gap-2">
                <CopyButton text={analysis} />
                <button
                  onClick={handleDownloadAnalysis}
                  className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                >
                  <Download className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
            <div className="p-4">
              <p className="text-sm whitespace-pre-wrap leading-relaxed">{analysis}</p>
            </div>
          </div>
        )}

        {extracted && (
          <div className="bg-card border border-border rounded-xl overflow-hidden">
            <div className="flex items-center justify-between px-4 py-2 border-b border-border bg-muted/40">
              <span className="font-medium text-sm flex items-center gap-1.5">
                <ScanText className="h-4 w-4" /> Extracted text
                <span className="text-xs text-muted-foreground font-normal">
                  {extracted.charCount.toLocaleString()} chars · selector: {extracted.selector}
                </span>
              </span>
              <CopyButton text={extracted.text} />
            </div>
            <pre className="text-xs font-mono p-4 bg-muted/30 overflow-auto max-h-72 whitespace-pre-wrap">
              {extracted.text}
            </pre>
          </div>
        )}

        {!screenshot && !analysis && !extracted && !isLoading && (
          <div className="flex flex-col items-center justify-center h-48 text-muted-foreground text-sm gap-2 border-2 border-dashed border-border rounded-xl">
            <Eye className="h-8 w-8 opacity-30" />
            <span>Results will appear here</span>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Batch Analysis Tab ────────────────────────────────────────────────────────

function BatchTab({ disabled }: { disabled: boolean }) {
  const [urlsText, setUrlsText] = useState('');
  const [question, setQuestion] = useState(QUESTION_PRESETS[0]);
  const [results, setResults] = useState<
    Array<{ url: string; success: boolean; analysis: string; screenshot_b64: string; error: string | null }>
  >([]);

  const urls = urlsText
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l.startsWith('http://') || l.startsWith('https://'));

  const batchMutation = useMutation({
    mutationFn: () => perceptionApi.batchAnalyze(urls, question),
    onSuccess: (r) => setResults(r.results),
    onError: () => toast({ kind: 'error', message: 'Batch analysis failed.' }),
  });

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <label htmlFor="batch-urls" className="block text-sm font-medium mb-1">
            URLs <span className="text-muted-foreground font-normal">(one per line, max 10)</span>
          </label>
          <textarea
            id="batch-urls"
            value={urlsText}
            onChange={(e) => setUrlsText(e.target.value)}
            rows={6}
            placeholder={'https://example.com\nhttps://docs.example.com/api\nhttps://blog.example.com'}
            className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background font-mono resize-none focus:ring-2 focus:ring-primary/30 focus:outline-none"
          />
          <p className="text-xs text-muted-foreground mt-1">
            {urls.length} valid URL{urls.length !== 1 ? 's' : ''} detected
          </p>
        </div>
        <div className="space-y-3">
          <div>
            <label htmlFor="batch-question" className="block text-sm font-medium mb-1">
              Question for all pages
            </label>
            <textarea
              id="batch-question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              rows={3}
              className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background resize-none"
            />
          </div>
          <button
            data-testid="btn-run-batch"
            onClick={() => batchMutation.mutate()}
            disabled={urls.length === 0 || urls.length > 10 || disabled || batchMutation.isPending}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50 hover:bg-primary/90"
          >
            {batchMutation.isPending ? (
              <><Loader2 className="h-4 w-4 animate-spin" /> Analyzing…</>
            ) : (
              <><Globe className="h-4 w-4" /> Run Batch ({urls.length})</>
            )}
          </button>
        </div>
      </div>

      {results.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="font-medium text-sm">
              Results — {results.filter((r) => r.success).length}/{results.length} succeeded
            </h3>
            <button
              onClick={() => setResults([])}
              className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1"
            >
              <Trash2 className="h-3.5 w-3.5" /> Clear
            </button>
          </div>
          {results.map((r) => (
            <div
              key={r.url}
              className={`border rounded-xl overflow-hidden ${r.success ? 'border-border' : 'border-destructive/40'}`}
            >
              <div className="flex items-center gap-2 px-4 py-2 bg-muted/40 border-b border-border">
                {r.success ? (
                  <CheckCircle className="h-4 w-4 text-green-500 shrink-0" />
                ) : (
                  <XCircle className="h-4 w-4 text-destructive shrink-0" />
                )}
                <a
                  href={r.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-sm font-mono text-blue-500 hover:underline truncate"
                >
                  {r.url}
                </a>
                {r.success && <CopyButton text={r.analysis} />}
              </div>
              <div className="p-4 grid grid-cols-1 md:grid-cols-3 gap-3">
                {r.screenshot_b64 && (
                  <div className="md:col-span-1">
                    <img
                      src={`data:image/png;base64,${r.screenshot_b64}`}
                      alt={`Screenshot of ${r.url}`}
                      className="w-full rounded border border-border"
                    />
                  </div>
                )}
                <div className={r.screenshot_b64 ? 'md:col-span-2' : 'md:col-span-3'}>
                  {r.success ? (
                    <p className="text-sm whitespace-pre-wrap leading-relaxed">{r.analysis}</p>
                  ) : (
                    <p className="text-sm text-destructive">{r.error ?? 'Unknown error'}</p>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── History Tab ───────────────────────────────────────────────────────────────

function HistoryTab({ refresh }: { refresh: number }) {
  const [entries, setEntries] = useState<HistoryEntry[]>(() => loadHistory());

  // Re-read on every refresh increment
  const prevRefresh = useRef(refresh);
  if (refresh !== prevRefresh.current) {
    prevRefresh.current = refresh;
    setEntries(loadHistory());
  }

  const handleClear = () => {
    localStorage.removeItem(HISTORY_KEY);
    setEntries([]);
  };

  if (entries.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-48 text-muted-foreground gap-2">
        <History className="h-8 w-8 opacity-30" />
        <p className="text-sm">No analysis history yet.</p>
        <p className="text-xs">Run Screenshot, Analyze, or Extract to populate history.</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">{entries.length} past analyses (last 10 kept)</p>
        <button
          onClick={handleClear}
          className="text-xs text-muted-foreground hover:text-destructive flex items-center gap-1"
        >
          <Trash2 className="h-3.5 w-3.5" /> Clear all
        </button>
      </div>
      {entries.map((e) => (
        <div key={e.id} className="border border-border rounded-xl overflow-hidden">
          <div className="flex items-start justify-between gap-3 px-4 py-3 bg-muted/30 border-b border-border">
            <div className="min-w-0">
              <a
                href={e.url}
                target="_blank"
                rel="noreferrer"
                className="text-sm font-medium text-blue-500 hover:underline truncate block"
              >
                {e.url}
              </a>
              <p className="text-xs text-muted-foreground mt-0.5 italic">{e.question}</p>
            </div>
            <span className="text-xs text-muted-foreground shrink-0 whitespace-nowrap">
              {new Date(e.timestamp).toLocaleString()}
            </span>
          </div>
          <div className="p-4 grid grid-cols-1 md:grid-cols-3 gap-3">
            {e.screenshot_b64 && (
              <div>
                <img
                  src={`data:image/png;base64,${e.screenshot_b64}`}
                  alt="History screenshot"
                  className="w-full rounded border border-border"
                />
              </div>
            )}
            <div className={e.screenshot_b64 ? 'md:col-span-2' : 'md:col-span-3'}>
              {e.analysis && (
                <div className="mb-2">
                  <p className="text-xs font-medium text-muted-foreground mb-1 flex items-center gap-1">
                    <Sparkles className="h-3 w-3" /> Analysis
                  </p>
                  <p className="text-sm text-foreground/80 line-clamp-4">{e.analysis}</p>
                </div>
              )}
              {e.extracted && (
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1 flex items-center gap-1">
                    <ScanText className="h-3 w-3" /> Extracted ({e.extracted.charCount} chars)
                  </p>
                  <p className="text-xs font-mono text-foreground/70 line-clamp-3">{e.extracted.text}</p>
                </div>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export function PerceptionPage() {
  const [activeTab, setActiveTab] = useState<Tab>('single');
  const [historyRefresh, setHistoryRefresh] = useState(0);

  const { data: status } = useQuery({
    queryKey: ['perception-status'],
    queryFn: () => perceptionApi.status(),
    staleTime: 30_000,
  });

  const playwrightOk = status?.playwright_available ?? false;
  const visionOk = status?.vision_available ?? false;
  const playwrightOff = status !== undefined && !playwrightOk;

  const triggerHistoryRefresh = useCallback(() => setHistoryRefresh((n) => n + 1), []);

  const tabs: Array<{ id: Tab; label: string; icon: React.ReactNode }> = [
    { id: 'single', label: 'Single Analysis', icon: <Eye className="h-4 w-4" /> },
    { id: 'batch', label: 'Batch Analysis', icon: <Globe className="h-4 w-4" /> },
    { id: 'history', label: 'History', icon: <History className="h-4 w-4" /> },
  ];

  return (
    <div className="space-y-5">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Eye className="h-6 w-6 text-violet-500" />
          Web Perception Lab
        </h1>
        <p className="text-muted-foreground text-sm mt-1">
          Screenshot, vision-analyze, and extract content from any web page
        </p>
      </div>

      {/* Status */}
      <StatusPanel playwrightOk={playwrightOk} visionOk={visionOk} />

      {/* Tabs */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="flex border-b border-border">
          {tabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setActiveTab(t.id)}
              className={`flex items-center gap-2 px-5 py-3 text-sm font-medium border-b-2 transition-colors ${
                activeTab === t.id
                  ? 'border-primary text-primary'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              {t.icon}
              {t.label}
              {t.id === 'history' && historyRefresh > 0 && (
                <span className="bg-primary/20 text-primary text-xs px-1.5 rounded-full">
                  {loadHistory().length}
                </span>
              )}
            </button>
          ))}
        </div>

        <div className="p-5">
          {activeTab === 'single' && (
            <SingleTab disabled={playwrightOff} onHistoryUpdate={triggerHistoryRefresh} />
          )}
          {activeTab === 'batch' && <BatchTab disabled={playwrightOff} />}
          {activeTab === 'history' && <HistoryTab refresh={historyRefresh} />}
        </div>
      </div>
    </div>
  );
}
