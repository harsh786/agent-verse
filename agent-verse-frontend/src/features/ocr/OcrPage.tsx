/**
 * OcrPage — World-Class OCR Document Extraction
 *
 * Tabs:
 *   Single  — drop zone → extract → result panel (type badge, confidence ring,
 *             field table, raw text accordion, JSON export)
 *   Batch   — multi-file drop, concurrent extraction, result card grid
 *   History — last 8 sessions persisted in localStorage
 *
 * Supported inputs: image/jpeg, image/png, image/webp, application/pdf
 * Backend: POST /ocr/extract  (multipart) | POST /ocr/batch (base64 JSON)
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertCircle, CheckCircle, ChevronDown, ChevronUp, ClipboardCopy,
  Database, Download, FileText, History, Layers, Loader2, RefreshCw,
  ScanText, Trash2, Upload, XCircle, Zap,
} from 'lucide-react';
import {
  ocrApi,
  knowledgeApi,
  type OcrDocumentType,
  type OcrFieldResult,
  type OcrResponse,
  type BatchOcrResponse,
} from '@/lib/api/client';
import { toast } from '@/stores/toast';
import { JARVISPageShell, JARVISStagger } from '@/components/ui/JARVISPageShell';

// ── Constants ─────────────────────────────────────────────────────────────────

const ACCEPTED_MIME = ['image/jpeg', 'image/png', 'image/webp', 'image/gif', 'application/pdf'];
const MAX_BATCH = 10;
const MAX_BYTES = 10 * 1024 * 1024; // 10 MB
const LS_KEY = 'ocr_history_v1';

const DOC_TYPE_LABELS: Record<OcrDocumentType, string> = {
  pan_card: 'PAN Card',
  aadhaar: 'Aadhaar',
  passport: 'Passport',
  driving_license: 'Driving Licence',
  voter_id: 'Voter ID',
  gstin_certificate: 'GSTIN Certificate',
  bank_cheque: 'Bank Cheque',
  salary_slip: 'Salary Slip',
  address_proof: 'Address Proof',
  invoice: 'Invoice',
  bank_statement: 'Bank Statement',
  receipt: 'Receipt',
  general: 'General Document',
};

const DOC_TYPE_COLORS: Record<OcrDocumentType, string> = {
  pan_card: 'bg-orange-500/20 text-orange-400 border-orange-500/40',
  aadhaar: 'bg-blue-500/20 text-blue-400 border-blue-500/40',
  passport: 'bg-purple-500/20 text-purple-400 border-purple-500/40',
  driving_license: 'bg-teal-500/20 text-teal-400 border-teal-500/40',
  voter_id: 'bg-green-500/20 text-green-400 border-green-500/40',
  gstin_certificate: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/40',
  bank_cheque: 'bg-cyan-500/20 text-cyan-400 border-cyan-500/40',
  salary_slip: 'bg-indigo-500/20 text-indigo-400 border-indigo-500/40',
  address_proof: 'bg-pink-500/20 text-pink-400 border-pink-500/40',
  invoice: 'bg-red-500/20 text-red-400 border-red-500/40',
  bank_statement: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40',
  receipt: 'bg-sky-500/20 text-sky-400 border-sky-500/40',
  general: 'bg-[#64748B]/20 text-[#94A3B8] border-[#64748B]/40',
};

// ── Types ─────────────────────────────────────────────────────────────────────

type Tab = 'single' | 'batch' | 'history';

interface HistoryEntry {
  id: string;
  filename: string;
  timestamp: number;
  result: OcrResponse;
}

interface BatchItem {
  id: string;
  file: File;
  status: 'pending' | 'loading' | 'done' | 'error';
  result?: OcrResponse | null;
  error?: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function confidenceColor(c: number): string {
  if (c >= 0.75) return 'bg-emerald-500';
  if (c >= 0.5) return 'bg-amber-500';
  return 'bg-red-500';
}

function confidenceText(c: number): string {
  if (c >= 0.75) return 'text-emerald-400';
  if (c >= 0.5) return 'text-amber-400';
  return 'text-red-400';
}

function pct(c: number): string {
  return `${Math.round(c * 100)}%`;
}

function copyText(text: string) {
  navigator.clipboard.writeText(text).then(() => toast({ kind: 'success', message: 'Copied to clipboard' }));
}

function loadHistory(): HistoryEntry[] {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) ?? '[]');
  } catch {
    return [];
  }
}

function saveHistory(entries: HistoryEntry[]) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(entries.slice(0, 8)));
  } catch { /* ignore */ }
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((res, rej) => {
    const reader = new FileReader();
    reader.onload = () => res((reader.result as string).split(',')[1] ?? '');
    reader.onerror = () => rej(new Error('Failed to read file'));
    reader.readAsDataURL(file);
  });
}

function validateFile(file: File): string | null {
  if (!ACCEPTED_MIME.includes(file.type)) {
    return `Unsupported type: ${file.type}. Use JPEG, PNG, WebP, GIF, or PDF.`;
  }
  if (file.size > MAX_BYTES) {
    return `File too large (${(file.size / 1024 / 1024).toFixed(1)} MB). Max 10 MB.`;
  }
  return null;
}

// ── Sub-components ────────────────────────────────────────────────────────────

function DropZone({
  onFile,
  disabled,
  label = 'Drop an image or PDF here, or click to browse',
}: {
  onFile: (file: File) => void;
  disabled?: boolean;
  label?: string;
}) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      if (disabled) return;
      const f = e.dataTransfer.files[0];
      if (f) onFile(f);
    },
    [disabled, onFile],
  );

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label="File drop zone"
      data-testid="drop-zone"
      onClick={() => !disabled && inputRef.current?.click()}
      onKeyDown={(e) => e.key === 'Enter' && !disabled && inputRef.current?.click()}
      onDragOver={(e) => { e.preventDefault(); if (!disabled) setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      className={[
        'flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed',
        'cursor-pointer select-none px-6 py-12 text-center transition-[color,background-color,border-color,opacity,box-shadow,transform] duration-200',
        dragging
          ? 'border-indigo-500 bg-indigo-500/10 scale-[1.01]'
          : 'border-[#1E2535] bg-[#1A1F2E]/40 hover:border-[#3D4D6A] hover:bg-[#1A1F2E]/60',
        disabled ? 'pointer-events-none opacity-50' : '',
      ].join(' ')}
    >
      <Upload className={`h-10 w-10 ${dragging ? 'text-indigo-400' : 'text-[#5A7494]'}`} />
      <p className="text-sm text-[#94A3B8]">{label}</p>
      <p className="text-xs text-[#374151]">JPEG · PNG · WebP · GIF · PDF — max 10 MB</p>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED_MIME.join(',')}
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFile(f);
          e.target.value = '';
        }}
        data-testid="file-input"
      />
    </div>
  );
}

function ConfidenceRing({ value }: { value: number }) {
  const r = 30;
  const circ = 2 * Math.PI * r;
  const offset = circ - value * circ;
  const color = value >= 0.75 ? '#10b981' : value >= 0.5 ? '#f59e0b' : '#ef4444';

  return (
    <div className="relative flex h-20 w-20 items-center justify-center" data-testid="confidence-ring">
      <svg className="-rotate-90" width="80" height="80">
        <circle cx="40" cy="40" r={r} stroke="#2D3748" strokeWidth="6" fill="none" />
        <circle
          cx="40" cy="40" r={r}
          stroke={color} strokeWidth="6" fill="none"
          strokeDasharray={circ}
          strokeDashoffset={offset}
          strokeLinecap="round"
          style={{ transition: 'stroke-dashoffset 0.6s ease' }}
        />
      </svg>
      <span className="absolute text-sm font-bold" style={{ color }}>{pct(value)}</span>
    </div>
  );
}

function FieldRow({ name, field }: { name: string; field: OcrFieldResult }) {
  return (
    <tr className="border-b border-[#1E2535] hover:bg-[#1A1F2E]/30">
      <td className="py-2.5 pr-4 text-xs font-medium text-[#94A3B8] whitespace-nowrap capitalize">
        {name.replace(/_/g, ' ')}
      </td>
      <td className="py-2.5 pr-4 text-sm text-[#E2E8F0] font-mono max-w-[240px] break-all">
        {field.value || <span className="text-[#374151] italic">—</span>}
      </td>
      <td className="py-2.5 pr-4">
        <div className="flex items-center gap-2">
          <div className="h-1.5 w-20 rounded-full bg-[#252B3B]">
            <div
              className={`h-1.5 rounded-full transition-[color,background-color,border-color,opacity,box-shadow,transform] ${confidenceColor(field.confidence)}`}
              style={{ width: pct(field.confidence) }}
            />
          </div>
          <span className={`text-xs ${confidenceText(field.confidence)}`}>{pct(field.confidence)}</span>
        </div>
      </td>
      <td className="py-2.5 pr-2">
        {field.is_valid ? (
          <CheckCircle className="h-3.5 w-3.5 text-emerald-500" aria-label="valid" />
        ) : (
          <XCircle className="h-3.5 w-3.5 text-red-500" aria-label="invalid" />
        )}
      </td>
      <td className="py-2.5">
        <button
          onClick={() => copyText(field.value)}
          className="rounded p-1 text-[#374151] hover:text-[#CBD5E1] transition-colors"
          aria-label={`Copy ${name}`}
          title="Copy value"
        >
          <ClipboardCopy className="h-3.5 w-3.5" />
        </button>
      </td>
    </tr>
  );
}

// ── Save to Knowledge Base (WS-13: link the isolated OCR page back to the KB) ─
//
// HONESTY RULE: this posts the real extracted text to a real collection via
// the existing `POST /knowledge/ingest` endpoint (source_type: "ocr") — the
// same generic ingest path the Knowledge page's Ingest tab already uses. The
// collection list is fetched live; there is no fabricated "saved" state.

function SaveToKnowledgeBase({ result, filename }: { result: OcrResponse; filename: string }) {
  const qc = useQueryClient();
  const [collectionId, setCollectionId] = useState('');

  const { data: collections = [], isLoading: collectionsLoading } = useQuery({
    queryKey: ['knowledge-collections'],
    queryFn: () => knowledgeApi.listCollections(),
    staleTime: 30_000,
  });

  const saveMutation = useMutation({
    mutationFn: () =>
      knowledgeApi.ingest({
        collection_id: collectionId,
        source_type: 'ocr',
        content: result.raw_text,
        metadata: {
          filename,
          document_type: result.document_type,
          engine_used: result.engine_used,
          overall_confidence: result.overall_confidence,
        },
      }),
    onSuccess: (r) => {
      toast({ kind: 'success', message: `Saved to knowledge base — ${r.chunks_created} chunk${r.chunks_created !== 1 ? 's' : ''} indexed.` });
      void qc.invalidateQueries({ queryKey: ['knowledge-docs', collectionId] });
    },
    onError: (e) => toast({ kind: 'error', message: e instanceof Error ? e.message : 'Save to knowledge base failed' }),
  });

  if (!collectionsLoading && collections.length === 0) {
    return (
      <p className="text-xs text-[#5A7494]" data-testid="kb-no-collections">
        No knowledge collections yet — create one on the Knowledge page to save this extract.
      </p>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="kb-save-row">
      <Database className="h-3.5 w-3.5 text-[#5A7494] shrink-0" aria-hidden />
      <label htmlFor="ocr-kb-collection" className="sr-only">Knowledge collection</label>
      <select
        id="ocr-kb-collection"
        value={collectionId}
        onChange={(e) => setCollectionId(e.target.value)}
        disabled={collectionsLoading}
        className="px-2.5 py-1.5 text-xs rounded-lg border border-[#1E2535] bg-[#0F1117] text-[#CBD5E1] focus:outline-none focus:ring-2 focus:ring-indigo-500/50"
      >
        <option value="">{collectionsLoading ? 'Loading collections…' : 'Select collection…'}</option>
        {collections.map((c) => (
          <option key={c.collection_id} value={c.collection_id}>{c.name}</option>
        ))}
      </select>
      <button
        onClick={() => saveMutation.mutate()}
        disabled={!collectionId || saveMutation.isPending}
        data-testid="kb-save-btn"
        className="flex items-center gap-1.5 rounded-lg border border-[#1E2535] px-3 py-1.5 text-xs text-[#CBD5E1] hover:border-[#94A3B8] hover:text-[#F1F5F9] disabled:opacity-50 transition-colors"
      >
        {saveMutation.isPending
          ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
          : <Database className="h-3.5 w-3.5" />}
        Save to Knowledge Base
      </button>
    </div>
  );
}

function OcrResultPanel({
  result,
  filename,
  onReset,
  onSave,
}: {
  result: OcrResponse;
  filename: string;
  onReset: () => void;
  onSave: () => void;
}) {
  const [rawOpen, setRawOpen] = useState(false);
  const fields = Object.entries(result.fields);
  const validCount = fields.filter(([, f]) => f.is_valid).length;

  const exportJson = () => {
    const blob = new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${filename.replace(/\.[^.]+$/, '')}_ocr.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex flex-col gap-4" data-testid="ocr-result">
      {/* Header row */}
      <div className="flex flex-wrap items-center gap-4 rounded-xl border border-[#1E2535] bg-[#1A1F2E]/60 p-4">
        <ConfidenceRing value={result.overall_confidence} />

        <div className="flex flex-1 flex-col gap-2">
          <div className="flex flex-wrap gap-2">
            <span
              className={`inline-flex items-center gap-1 rounded-full border px-3 py-0.5 text-xs font-medium ${
                DOC_TYPE_COLORS[result.document_type] ?? DOC_TYPE_COLORS.general
              }`}
              data-testid="doc-type-badge"
            >
              <FileText className="h-3 w-3" />
              {DOC_TYPE_LABELS[result.document_type] ?? result.document_type}
            </span>

            <span className="inline-flex items-center gap-1 rounded-full border border-[#1E2535] bg-[#252B3B]/50 px-3 py-0.5 text-xs text-[#CBD5E1]">
              <Zap className="h-3 w-3 text-yellow-400" />
              {result.engine_used === 'tesseract' ? 'Tesseract' : 'LLM Vision'}
            </span>

            <span className="inline-flex items-center gap-1 rounded-full border border-[#1E2535] bg-[#252B3B]/50 px-3 py-0.5 text-xs text-[#CBD5E1]">
              <Layers className="h-3 w-3 text-sky-400" />
              {result.page_count} {result.page_count === 1 ? 'page' : 'pages'}
            </span>
          </div>

          <p className="text-xs text-[#5A7494]">
            {fields.length} fields extracted &nbsp;·&nbsp;
            <span className="text-emerald-400">{validCount} valid</span>
            {fields.length - validCount > 0 && (
              <span className="text-red-400">&nbsp;·&nbsp;{fields.length - validCount} invalid</span>
            )}
          </p>
        </div>

        <div className="flex gap-2">
          <button
            onClick={exportJson}
            className="flex items-center gap-1.5 rounded-lg border border-[#1E2535] px-3 py-1.5 text-xs text-[#CBD5E1] hover:border-[#94A3B8] hover:text-[#F1F5F9] transition-colors"
            data-testid="export-json"
          >
            <Download className="h-3.5 w-3.5" /> Export JSON
          </button>
          <button
            onClick={onSave}
            className="flex items-center gap-1.5 rounded-lg border border-[#1E2535] px-3 py-1.5 text-xs text-[#CBD5E1] hover:border-[#94A3B8] hover:text-[#F1F5F9] transition-colors"
          >
            <History className="h-3.5 w-3.5" /> Save
          </button>
          <button
            onClick={onReset}
            className="flex items-center gap-1.5 rounded-lg border border-[#1E2535] px-3 py-1.5 text-xs text-[#CBD5E1] hover:border-[#94A3B8] hover:text-[#F1F5F9] transition-colors"
            data-testid="new-extraction"
          >
            <RefreshCw className="h-3.5 w-3.5" /> New
          </button>
        </div>
      </div>

      {/* Fields table */}
      {fields.length > 0 && (
        <div className="rounded-xl border border-[#1E2535] bg-[#1A1F2E]/40 p-4">
          <h3 className="mb-3 text-sm font-semibold text-[#CBD5E1]">Extracted Fields</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-left" data-testid="fields-table">
              <thead>
                <tr className="border-b border-[#1E2535]">
                  <th className="pb-2 text-xs uppercase tracking-wider text-[#5A7494]">Field</th>
                  <th className="pb-2 text-xs uppercase tracking-wider text-[#5A7494]">Value</th>
                  <th className="pb-2 text-xs uppercase tracking-wider text-[#5A7494]">Confidence</th>
                  <th className="pb-2 text-xs uppercase tracking-wider text-[#5A7494]">Valid</th>
                  <th className="pb-2" />
                </tr>
              </thead>
              <tbody>
                {fields.map(([name, field]) => (
                  <FieldRow key={name} name={name} field={field} />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Save extract to the unified Knowledge Base (WS-13) */}
      <div className="rounded-xl border border-[#1E2535] bg-[#1A1F2E]/40 p-4">
        <SaveToKnowledgeBase result={result} filename={filename} />
      </div>

      {/* Raw text accordion */}
      <div className="rounded-xl border border-[#1E2535] bg-[#1A1F2E]/40">
        <button
          onClick={() => setRawOpen((o) => !o)}
          className="flex w-full items-center justify-between px-4 py-3 text-sm font-medium text-[#CBD5E1] hover:text-[#F1F5F9] transition-colors"
          data-testid="raw-text-toggle"
          aria-expanded={rawOpen}
        >
          <span className="flex items-center gap-2">
            <ScanText className="h-4 w-4 text-indigo-400" />
            Raw Extracted Text
            <span className="rounded-full bg-[#252B3B] px-2 py-0.5 text-xs text-[#94A3B8]">
              {result.raw_text.length} chars
            </span>
          </span>
          {rawOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </button>

        {rawOpen && (
          <div className="border-t border-[#1E2535] p-4">
            <div className="relative">
              <pre
                className="max-h-72 overflow-y-auto rounded-lg bg-[#0F1117] p-4 text-xs text-[#CBD5E1] leading-relaxed whitespace-pre-wrap break-words"
                data-testid="raw-text"
              >
                {result.raw_text || <span className="text-[#374151] italic">No text extracted</span>}
              </pre>
              <button
                onClick={() => copyText(result.raw_text)}
                className="absolute right-2 top-2 rounded-md bg-[#1A1F2E] p-1.5 text-[#5A7494] hover:text-[#CBD5E1] transition-colors"
                aria-label="Copy raw text"
                data-testid="copy-raw"
              >
                <ClipboardCopy className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function OcrPage() {
  const [tab, setTab] = useState<Tab>('single');

  // ── Single extraction state
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [result, setResult] = useState<OcrResponse | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>(loadHistory);

  // ── Batch state
  const [batchItems, setBatchItems] = useState<BatchItem[]>([]);
  const [batchResult, setBatchResult] = useState<BatchOcrResponse | null>(null);

  const extractMutation = useMutation({
    mutationFn: (f: File) => ocrApi.extractFile(f),
    onSuccess: (data) => setResult(data),
    onError: (e: Error) => toast({ kind: "error", message: e.message ?? 'Extraction failed' }),
  });

  const batchMutation = useMutation({
    mutationFn: async (items: BatchItem[]) => {
      // Update loading states per-item
      setBatchItems((prev) =>
        prev.map((i) =>
          items.some((ii) => ii.id === i.id) ? { ...i, status: 'loading' } : i,
        ),
      );
      const docs = await Promise.all(
        items.map(async (item) => {
          const isPdf = item.file.type === 'application/pdf';
          const b64 = await fileToBase64(item.file);
          return isPdf
            ? { pdf_base64: b64, filename: item.file.name }
            : { image_base64: b64, filename: item.file.name };
        }),
      );
      return ocrApi.batch(docs);
    },
    onSuccess: (data) => {
      setBatchResult(data);
      setBatchItems((prev) =>
        prev.map((item, idx) => ({
          ...item,
          status: data.results[idx] ? 'done' : 'error',
          result: data.results[idx],
          error: data.results[idx] ? undefined : 'Extraction failed',
        })),
      );
      toast({ kind: "success", message: `${data.succeeded}/${data.total} documents extracted` });
    },
    onError: (e: Error) => toast({ kind: "error", message: e.message ?? 'Batch extraction failed' }),
  });

  // Generate image preview
  useEffect(() => {
    if (!file) { setPreview(null); return; }
    if (file.type.startsWith('image/')) {
      const url = URL.createObjectURL(file);
      setPreview(url);
      return () => URL.revokeObjectURL(url);
    }
    setPreview(null);
  }, [file]);

  const handleSingleFile = useCallback((f: File) => {
    const err = validateFile(f);
    if (err) { toast({ kind: "error", message: err }); return; }
    setFile(f);
    setResult(null);
  }, []);

  const handleExtract = () => {
    if (!file) return;
    extractMutation.mutate(file);
  };

  const handleReset = () => {
    setFile(null);
    setResult(null);
    setPreview(null);
    extractMutation.reset();
  };

  const handleSaveToHistory = () => {
    if (!result || !file) return;
    const entry: HistoryEntry = {
      id: crypto.randomUUID(),
      filename: file.name,
      timestamp: Date.now(),
      result,
    };
    const next = [entry, ...history].slice(0, 8);
    setHistory(next);
    saveHistory(next);
    toast({ kind: "success", message: 'Saved to history' });
  };

  const handleBatchFiles = (files: File[]) => {
    const valid = files.filter((f) => {
      const err = validateFile(f);
      if (err) toast({ kind: "error", message: `${f.name}: ${err}` });
      return !err;
    }).slice(0, MAX_BATCH);
    const items: BatchItem[] = valid.map((f) => ({
      id: crypto.randomUUID(),
      file: f,
      status: 'pending',
    }));
    setBatchItems((prev) => [...prev, ...items].slice(0, MAX_BATCH));
  };

  const clearHistory = () => {
    setHistory([]);
    saveHistory([]);
    toast({ kind: "success", message: 'History cleared' });
  };

  const TABS: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: 'single', label: 'Single', icon: <ScanText className="h-4 w-4" /> },
    { id: 'batch', label: 'Batch', icon: <Layers className="h-4 w-4" /> },
    { id: 'history', label: `History (${history.length})`, icon: <History className="h-4 w-4" /> },
  ];

  return (
    <JARVISPageShell>
    <JARVISStagger className="flex flex-col gap-6 p-4 lg:p-6" data-testid="ocr-page">
      {/* Page header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[#00D4FF] flex items-center gap-2">
            <ScanText className="h-6 w-6 text-indigo-400" />
            OCR Document Extraction
          </h1>
          <p className="mt-1 text-sm text-[#94A3B8]">
            Extract text and structured fields from images and PDFs using Tesseract or LLM Vision
          </p>
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 rounded-xl border border-[#1E2535] bg-[#1A1F2E]/40 p-1">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            data-testid={`tab-${t.id}`}
            className={[
              'flex flex-1 items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-[color,background-color,border-color,opacity,box-shadow,transform]',
              tab === t.id
                ? 'bg-indigo-600 text-[#F1F5F9] shadow'
                : 'text-[#94A3B8] hover:text-[#E2E8F0]',
            ].join(' ')}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </div>

      {/* ── Single Tab ─────────────────────────────────────────────────────── */}
      {tab === 'single' && (
        <div className="flex flex-col gap-4">
          {!result && (
            <>
              {!file ? (
                <DropZone onFile={handleSingleFile} />
              ) : (
                <div className="flex flex-col gap-4 rounded-xl border border-[#1E2535] bg-[#1A1F2E]/40 p-4">
                  {/* Preview */}
                  <div className="flex items-center gap-4">
                    {preview ? (
                      <img
                        src={preview}
                        alt="Preview"
                        className="h-20 w-20 rounded-lg object-cover border border-[#1E2535]"
                        data-testid="image-preview"
                      />
                    ) : (
                      <div className="flex h-20 w-20 items-center justify-center rounded-lg border border-[#1E2535] bg-[#1A1F2E]">
                        <FileText className="h-8 w-8 text-[#5A7494]" />
                      </div>
                    )}
                    <div className="flex flex-col gap-1">
                      <p className="font-medium text-[#E2E8F0]" data-testid="filename">
                        {file.name}
                      </p>
                      <p className="text-xs text-[#5A7494]">
                        {(file.size / 1024).toFixed(1)} KB · {file.type}
                      </p>
                    </div>
                    <button
                      onClick={handleReset}
                      className="ml-auto rounded-lg p-2 text-[#5A7494] hover:text-red-400 transition-colors"
                      aria-label="Remove file"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>

                  <button
                    onClick={handleExtract}
                    disabled={extractMutation.isPending}
                    className="flex items-center justify-center gap-2 rounded-lg bg-indigo-600 px-6 py-3 text-sm font-semibold text-[#F1F5F9] hover:bg-indigo-500 disabled:opacity-60 transition-colors"
                    data-testid="extract-btn"
                  >
                    {extractMutation.isPending ? (
                      <>
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Extracting…
                      </>
                    ) : (
                      <>
                        <ScanText className="h-4 w-4" />
                        Extract Document
                      </>
                    )}
                  </button>

                  {extractMutation.isPending && (
                    <div
                      className="flex items-center gap-2 text-sm text-[#94A3B8]"
                      data-testid="loading-indicator"
                    >
                      <div className="h-1.5 flex-1 rounded-full bg-[#252B3B] overflow-hidden">
                        <div className="h-1.5 w-1/3 rounded-full bg-indigo-500 animate-pulse" />
                      </div>
                    </div>
                  )}

                  {extractMutation.isError && (
                    <div
                      className="flex items-center gap-2 rounded-lg border border-red-800 bg-red-900/20 px-4 py-3 text-sm text-red-400"
                      data-testid="error-message"
                    >
                      <AlertCircle className="h-4 w-4 shrink-0" />
                      {(extractMutation.error as Error).message}
                    </div>
                  )}
                </div>
              )}
            </>
          )}

          {result && (
            <OcrResultPanel
              result={result}
              filename={file?.name ?? 'document'}
              onReset={handleReset}
              onSave={handleSaveToHistory}
            />
          )}
        </div>
      )}

      {/* ── Batch Tab ──────────────────────────────────────────────────────── */}
      {tab === 'batch' && (
        <div className="flex flex-col gap-4">
          <DropZone
            label={`Drop up to ${MAX_BATCH} files — images or PDFs`}
            onFile={(f) => handleBatchFiles([f])}
            disabled={batchItems.length >= MAX_BATCH}
          />

          {/* Support multi-file drag */}
          <div
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              handleBatchFiles(Array.from(e.dataTransfer.files));
            }}
            className="hidden"
          />

          {batchItems.length > 0 && (
            <>
              <div className="flex items-center justify-between">
                <p className="text-sm text-[#94A3B8]">
                  {batchItems.length}/{MAX_BATCH} files queued
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={() => { setBatchItems([]); setBatchResult(null); }}
                    className="text-xs text-[#5A7494] hover:text-red-400 transition-colors"
                  >
                    Clear all
                  </button>
                  <button
                    onClick={() => batchMutation.mutate(batchItems.filter((i) => i.status === 'pending'))}
                    disabled={batchMutation.isPending || batchItems.every((i) => i.status !== 'pending')}
                    className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-medium text-[#F1F5F9] hover:bg-indigo-500 disabled:opacity-60 transition-colors"
                    data-testid="extract-batch-btn"
                  >
                    {batchMutation.isPending ? (
                      <><Loader2 className="h-3.5 w-3.5 animate-spin" /> Processing…</>
                    ) : (
                      <><Zap className="h-3.5 w-3.5" /> Extract All</>
                    )}
                  </button>
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" data-testid="batch-grid">
                {batchItems.map((item) => (
                  <div
                    key={item.id}
                    className="flex flex-col gap-3 rounded-xl border border-[#1E2535] bg-[#1A1F2E]/40 p-4"
                    data-testid={`batch-item-${item.status}`}
                  >
                    <div className="flex items-center gap-2">
                      <FileText className="h-5 w-5 shrink-0 text-[#5A7494]" />
                      <p className="truncate text-sm font-medium text-[#E2E8F0]">{item.file.name}</p>
                      {item.status === 'pending' && (
                        <button
                          onClick={() => setBatchItems((p) => p.filter((i) => i.id !== item.id))}
                          className="ml-auto shrink-0 text-[#374151] hover:text-red-400 transition-colors"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      )}
                      {item.status === 'loading' && <Loader2 className="ml-auto h-4 w-4 animate-spin text-indigo-400" />}
                      {item.status === 'done' && <CheckCircle className="ml-auto h-4 w-4 text-emerald-500" />}
                      {item.status === 'error' && <XCircle className="ml-auto h-4 w-4 text-red-500" />}
                    </div>

                    {item.result && (
                      <div className="flex flex-col gap-1 rounded-lg bg-[#0F1117]/50 p-2">
                        <span
                          className={`self-start rounded-full border px-2 py-0.5 text-xs font-medium ${
                            DOC_TYPE_COLORS[item.result.document_type] ?? DOC_TYPE_COLORS.general
                          }`}
                        >
                          {DOC_TYPE_LABELS[item.result.document_type] ?? item.result.document_type}
                        </span>
                        <p className="text-xs text-[#94A3B8]">
                          Confidence: <span className={confidenceText(item.result.overall_confidence)}>{pct(item.result.overall_confidence)}</span>
                          &nbsp;·&nbsp;{Object.keys(item.result.fields).length} fields
                        </p>
                      </div>
                    )}

                    {item.error && (
                      <p className="text-xs text-red-400">{item.error}</p>
                    )}
                  </div>
                ))}
              </div>

              {batchResult && (
                <div className="rounded-xl border border-[#1E2535] bg-[#1A1F2E]/40 px-4 py-3 text-sm text-[#CBD5E1]">
                  Batch complete: <span className="text-emerald-400 font-medium">{batchResult.succeeded}</span> succeeded,{' '}
                  <span className={batchResult.failed > 0 ? 'text-red-400 font-medium' : 'text-[#94A3B8]'}>
                    {batchResult.failed}
                  </span>{' '}
                  failed out of {batchResult.total} documents.
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* ── History Tab ────────────────────────────────────────────────────── */}
      {tab === 'history' && (
        <div className="flex flex-col gap-4">
          {history.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-[#1E2535] py-16 text-center">
              <History className="h-10 w-10 text-[#374151]" />
              <p className="text-sm text-[#5A7494]">No extraction history yet</p>
              <p className="text-xs text-[#374151]">Save results from the Single tab to see them here</p>
            </div>
          ) : (
            <>
              <div className="flex justify-end">
                <button
                  onClick={clearHistory}
                  className="text-xs text-[#5A7494] hover:text-red-400 transition-colors"
                >
                  Clear history
                </button>
              </div>
              <div className="flex flex-col gap-3" data-testid="history-list">
                {history.map((entry) => (
                  <div
                    key={entry.id}
                    className="flex flex-col gap-2 rounded-xl border border-[#1E2535] bg-[#1A1F2E]/40 p-4"
                    data-testid="history-entry"
                  >
                    <div className="flex items-center justify-between gap-4">
                      <div className="flex items-center gap-2 min-w-0">
                        <FileText className="h-4 w-4 shrink-0 text-[#5A7494]" />
                        <span className="truncate text-sm font-medium text-[#E2E8F0]">{entry.filename}</span>
                      </div>
                      <span className="shrink-0 text-xs text-[#5A7494]">
                        {new Date(entry.timestamp).toLocaleString()}
                      </span>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <span
                        className={`rounded-full border px-2 py-0.5 text-xs font-medium ${
                          DOC_TYPE_COLORS[entry.result.document_type] ?? DOC_TYPE_COLORS.general
                        }`}
                      >
                        {DOC_TYPE_LABELS[entry.result.document_type] ?? entry.result.document_type}
                      </span>
                      <span className={`text-xs ${confidenceText(entry.result.overall_confidence)}`}>
                        {pct(entry.result.overall_confidence)} confidence
                      </span>
                      <span className="text-xs text-[#5A7494]">
                        {Object.keys(entry.result.fields).length} fields
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </JARVISStagger>
    </JARVISPageShell>
  );
}
