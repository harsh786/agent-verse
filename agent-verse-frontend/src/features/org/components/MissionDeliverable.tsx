/**
 * MissionDeliverable — rich, format-aware viewer for a mission's output.
 *
 * finalize_mission writes the mission's `outputs` (each an aggregated report that
 * carries a structured `deliverable`) and `evidence`. The deliverable can hold a
 * markdown summary, metric cards, tables, a verification note, and tool evidence —
 * so instead of dumping raw JSON (the old "blackbox"), this renders every part in
 * its natural format, with a raw-JSON toggle plus copy/download for power users.
 *
 * Pure presentational (no fetching) so it unit-tests in isolation. Renders nothing
 * when there is no deliverable yet — nothing is fabricated.
 */
import { useMemo, useState, type ReactNode } from 'react';
import Markdown from 'react-markdown';
import {
  Check,
  ChevronDown,
  Copy,
  Download,
  ExternalLink,
  FileText,
  Link as LinkIcon,
  Paperclip,
  Send,
  Share2,
  ShieldCheck,
  Wrench,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import type { MissionPublishReceipt } from '../types';

type Metric = { label?: string; value?: unknown };
type TableLike =
  | { title?: string; columns?: string[]; rows?: unknown[][] }
  | Record<string, unknown>[];
type FileArtifact = { name?: string; filename?: string; url?: string; href?: string; mime?: string; content_type?: string; size?: number };
type ImageArtifact = string | { url?: string; src?: string; caption?: string; alt?: string };
type LinkArtifact = string | { url?: string; href?: string; title?: string; label?: string };
interface Deliverable {
  kind?: string;
  title?: string;
  status?: string;
  summary?: string;
  content?: string;
  text?: string;
  metrics?: Metric[];
  tables?: TableLike[];
  files?: FileArtifact[];
  artifacts?: FileArtifact[];
  images?: ImageArtifact[];
  links?: LinkArtifact[];
  evidence?: { tools?: unknown[]; verification?: string } | unknown;
  [k: string]: unknown;
}

const isUrl = (v: unknown): v is string =>
  typeof v === 'string' && /^(https?:|data:)/i.test(v.trim());

function fmtBytes(n: number): string {
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)}KB`;
  return `${(n / (1024 * 1024)).toFixed(1)}MB`;
}

/** Tailwind-styled elements for the markdown body (bullets, bold, code, links…). */
const MD_COMPONENTS = {
  p: (p: { children?: ReactNode }) => <p className="mb-2 last:mb-0">{p.children}</p>,
  strong: (p: { children?: ReactNode }) => (
    <strong className="font-semibold text-[#F1F5F9]">{p.children}</strong>
  ),
  ul: (p: { children?: ReactNode }) => (
    <ul className="list-disc pl-5 space-y-1 mb-2">{p.children}</ul>
  ),
  ol: (p: { children?: ReactNode }) => (
    <ol className="list-decimal pl-5 space-y-1 mb-2">{p.children}</ol>
  ),
  li: (p: { children?: ReactNode }) => <li className="marker:text-[#475569]">{p.children}</li>,
  h1: (p: { children?: ReactNode }) => (
    <h3 className="text-[15px] font-semibold text-[#F1F5F9] mt-3 mb-1.5">{p.children}</h3>
  ),
  h2: (p: { children?: ReactNode }) => (
    <h3 className="text-[14px] font-semibold text-[#F1F5F9] mt-3 mb-1.5">{p.children}</h3>
  ),
  h3: (p: { children?: ReactNode }) => (
    <h4 className="text-[13px] font-semibold text-[#E2E8F0] mt-2 mb-1">{p.children}</h4>
  ),
  a: (p: { href?: string; children?: ReactNode }) => (
    <a href={p.href} target="_blank" rel="noreferrer" className="text-[#00D4FF] hover:underline">
      {p.children}
    </a>
  ),
  code: (p: { children?: ReactNode }) => (
    <code className="text-[12px] bg-[#0B0E14] border border-[#1E2535] rounded px-1 py-0.5 text-[#E2E8F0]">
      {p.children}
    </code>
  ),
  table: (p: { children?: ReactNode }) => (
    <div className="overflow-x-auto my-2">
      <table className="w-full text-[12px] border border-[#1E2535]">{p.children}</table>
    </div>
  ),
  th: (p: { children?: ReactNode }) => (
    <th className="text-left font-medium text-[#94A3B8] px-2 py-1 border border-[#1E2535] bg-[#0F1117]">
      {p.children}
    </th>
  ),
  td: (p: { children?: ReactNode }) => (
    <td className="text-[#CBD5E1] px-2 py-1 border border-[#1E2535]">{p.children}</td>
  ),
};

/** Pull the structured deliverable out of a raw output entry. */
function normalize(output: unknown): Deliverable {
  if (typeof output === 'string') return { kind: 'text', summary: output };
  if (output && typeof output === 'object') {
    const o = output as Record<string, unknown>;
    if (o.deliverable && typeof o.deliverable === 'object') {
      return o.deliverable as Deliverable;
    }
    return o as Deliverable;
  }
  return { summary: String(output) };
}

function bodyText(d: Deliverable): string {
  let s = (d.summary ?? d.content ?? d.text ?? '') as string;
  if (typeof s !== 'string') return String(s ?? '');
  // The summary is sometimes a wrapped tool result — `{"tool": ..., "result": "…"}`.
  // Unwrap it so the reader sees the answer, not JSON plumbing.
  const trimmed = s.trim();
  if (trimmed.startsWith('{') && trimmed.includes('"result"')) {
    try {
      const parsed = JSON.parse(trimmed) as { result?: unknown };
      if (parsed && typeof parsed.result === 'string') s = parsed.result;
    } catch {
      /* not valid JSON — leave as-is */
    }
  }
  return s;
}

export function MissionDeliverable({
  outputs,
  evidence,
  published,
  publishPending,
}: {
  outputs: unknown[];
  evidence: unknown[];
  /** Receipt proving the deliverable was published (Phase 2 autonomous publish). */
  published?: MissionPublishReceipt | null;
  /** A finished deliverable is waiting at the publish approval gate. */
  publishPending?: boolean;
}) {
  const deliverables = useMemo(() => (outputs ?? []).map(normalize), [outputs]);
  if (deliverables.length === 0 && !published && !publishPending) return null;

  return (
    <div className="space-y-3">
      {/* Published-to receipt is the proof-of-outcome — it leads. */}
      {published && <PublishReceiptBanner receipt={published} />}
      {!published && publishPending && <PublishPendingBanner />}
      {deliverables.map((d, i) => (
        <DeliverableCard key={i} d={d} raw={outputs[i]} index={i} total={deliverables.length} />
      ))}
      {evidence.length > 0 && <EvidenceStrip evidence={evidence} />}
    </div>
  );
}

/** Extract a human-facing link from a publish receipt (the place it landed). */
function receiptLink(receipt: MissionPublishReceipt): string | null {
  const out = receipt.output as Record<string, unknown> | undefined;
  if (out && typeof out === 'object') {
    // Common connector shapes: {url}, {permalink}, {link}, {body:{url}} (httpbin echoes the request url).
    for (const k of ['url', 'permalink', 'link', 'html_url']) {
      if (isUrl(out[k])) return out[k] as string;
    }
    const body = out.body as Record<string, unknown> | undefined;
    if (body && typeof body === 'object') {
      for (const k of ['url', 'permalink', 'link']) if (isUrl(body[k])) return body[k] as string;
    }
  }
  return null;
}

function PublishReceiptBanner({ receipt }: { receipt: MissionPublishReceipt }) {
  const ok = receipt.success;
  const link = receiptLink(receipt);
  const when = (() => {
    try { return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(receipt.published_at)); }
    catch { return receipt.published_at; }
  })();
  return (
    <div
      className={cn(
        'rounded-xl border p-3 flex items-start gap-3',
        ok ? 'border-blue-500/25 bg-blue-500/[0.06]' : 'border-rose-500/25 bg-rose-500/[0.06]',
      )}
    >
      <div className={cn('h-8 w-8 rounded-lg flex items-center justify-center shrink-0',
        ok ? 'bg-blue-500/15 text-blue-300' : 'bg-rose-500/15 text-rose-300')}>
        <Send className="h-4 w-4" aria-hidden />
      </div>
      <div className="flex-1 min-w-0">
        <p className={cn('text-[13px] font-semibold', ok ? 'text-blue-200' : 'text-rose-200')}>
          {ok ? 'Published' : 'Publish failed'} · <span className="font-mono">{receipt.tool_name}</span>
        </p>
        <p className="text-[11px] text-[#94A3B8] mt-0.5">
          {ok ? `Sent via ${receipt.server_id} · ${when}` : (receipt.error || 'The connector returned an error.')}
        </p>
        {link && (
          <a
            href={link}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 mt-1.5 text-[12px] text-[#00D4FF] hover:underline break-all"
          >
            <ExternalLink className="h-3 w-3 shrink-0" aria-hidden />
            {link}
          </a>
        )}
      </div>
    </div>
  );
}

function PublishPendingBanner() {
  return (
    <div className="rounded-xl border border-amber-500/25 bg-amber-500/[0.06] p-3 flex items-center gap-3">
      <div className="h-8 w-8 rounded-lg bg-amber-500/15 text-amber-300 flex items-center justify-center shrink-0">
        <Send className="h-4 w-4" aria-hidden />
      </div>
      <p className="text-[12px] text-amber-200">
        Deliverable ready — <span className="font-semibold">publishing is awaiting your approval</span>.
        Approve it on the schedule to send this and future runs automatically.
      </p>
    </div>
  );
}

function DeliverableCard({
  d,
  raw,
  index,
  total,
}: {
  d: Deliverable;
  raw: unknown;
  index: number;
  total: number;
}) {
  const [showRaw, setShowRaw] = useState(false);
  const [copied, setCopied] = useState(false);
  const body = bodyText(d);
  const metrics = Array.isArray(d.metrics) ? d.metrics.filter((m) => m && m.label != null) : [];
  const tables = Array.isArray(d.tables) ? d.tables.filter((t) => t != null) : [];
  const ok = (d.status ?? 'success').toString().toLowerCase() === 'success';
  const ev = (d.evidence ?? {}) as { tools?: unknown[]; verification?: string };
  const isCode = (d.kind ?? '').toString().toLowerCase() === 'code';

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(body || JSON.stringify(raw, null, 2));
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      /* clipboard unavailable */
    }
  };
  const download = () => {
    try {
      const blob = new Blob([JSON.stringify(raw, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `deliverable-${index + 1}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      /* download blocked */
    }
  };
  const [shared, setShared] = useState(false);
  const share = async () => {
    const text = body || JSON.stringify(raw, null, 2);
    const title = (d.title as string) || 'Mission deliverable';
    try {
      // Native share sheet where available (mobile / supporting browsers).
      if (navigator.share) {
        await navigator.share({ title, text });
        return;
      }
    } catch {
      /* user cancelled or share failed — fall through to copy */
    }
    try {
      await navigator.clipboard.writeText(text);
      setShared(true);
      setTimeout(() => setShared(false), 1400);
    } catch {
      /* clipboard unavailable */
    }
  };
  const files = [
    ...(Array.isArray(d.files) ? d.files : []),
    ...(Array.isArray(d.artifacts) ? d.artifacts : []),
  ].filter((f) => f && (f.url || f.href));
  const images = (Array.isArray(d.images) ? d.images : []).filter(
    (im) => (typeof im === 'string' ? isUrl(im) : isUrl(im?.url) || isUrl(im?.src)),
  );
  const links = (Array.isArray(d.links) ? d.links : []).filter(
    (l) => (typeof l === 'string' ? isUrl(l) : isUrl(l?.url) || isUrl(l?.href)),
  );

  return (
    <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/[0.04] overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-emerald-500/15 bg-emerald-500/[0.06]">
        <FileText className="h-3.5 w-3.5 text-emerald-400 shrink-0" aria-hidden />
        <span className="text-[11px] font-semibold text-emerald-300 uppercase tracking-wider">
          Deliverable{total > 1 ? ` ${index + 1}/${total}` : ''}
        </span>
        <span
          className={cn(
            'text-[9px] font-semibold uppercase px-1.5 py-0.5 rounded-full',
            ok ? 'bg-emerald-500/15 text-emerald-300' : 'bg-red-500/15 text-red-300',
          )}
        >
          {ok ? 'success' : (d.status as string) || 'failed'}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={copy}
            title="Copy result"
            className="p-1.5 rounded-md text-[#94A3B8] hover:text-[#F1F5F9] hover:bg-white/5 transition-colors"
            aria-label="Copy result"
          >
            {copied ? (
              <Check className="h-3.5 w-3.5 text-emerald-400" aria-hidden />
            ) : (
              <Copy className="h-3.5 w-3.5" aria-hidden />
            )}
          </button>
          <button
            onClick={share}
            title="Share result"
            className="p-1.5 rounded-md text-[#94A3B8] hover:text-[#F1F5F9] hover:bg-white/5 transition-colors"
            aria-label="Share result"
          >
            {shared ? (
              <Check className="h-3.5 w-3.5 text-emerald-400" aria-hidden />
            ) : (
              <Share2 className="h-3.5 w-3.5" aria-hidden />
            )}
          </button>
          <button
            onClick={download}
            title="Download JSON"
            className="p-1.5 rounded-md text-[#94A3B8] hover:text-[#F1F5F9] hover:bg-white/5 transition-colors"
            aria-label="Download deliverable as JSON"
          >
            <Download className="h-3.5 w-3.5" aria-hidden />
          </button>
        </div>
      </div>

      <div className="p-4 space-y-4">
        {/* Title / objective */}
        {d.title && (
          <p className="text-[12px] leading-snug text-[#CBD5E1] font-medium">{d.title as string}</p>
        )}

        {/* Metric cards */}
        {metrics.length > 0 && (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {metrics.map((m, i) => (
              <div
                key={i}
                className="rounded-lg border border-[#1E2535] bg-[#0F1117] px-3 py-2"
              >
                <p className="text-[9px] uppercase tracking-wide text-[#475569]">{m.label}</p>
                <p className="text-[15px] font-semibold text-[#F1F5F9] tabular-nums">
                  {typeof m.value === 'object' ? JSON.stringify(m.value) : String(m.value ?? '—')}
                </p>
              </div>
            ))}
          </div>
        )}

        {/* Main body — markdown, or code block */}
        {body &&
          (isCode ? (
            <pre className="text-[12px] leading-relaxed text-[#E2E8F0] bg-[#0B0E14] border border-[#1E2535] rounded-lg p-3 overflow-x-auto">
              <code>{body}</code>
            </pre>
          ) : (
            <div className="text-[13px] leading-relaxed text-[#CBD5E1]">
              <Markdown components={MD_COMPONENTS}>{body}</Markdown>
            </div>
          ))}

        {/* Tables */}
        {tables.map((t, i) => (
          <DeliverableTable key={i} table={t} />
        ))}

        {/* Images */}
        {images.length > 0 && (
          <div className="grid grid-cols-2 gap-2">
            {images.map((im, i) => {
              const src = typeof im === 'string' ? im : (im.url ?? im.src ?? '');
              const cap = typeof im === 'string' ? '' : (im.caption ?? im.alt ?? '');
              return (
                <figure key={i} className="rounded-lg border border-[#1E2535] overflow-hidden bg-[#0B0E14]">
                  <img src={src} alt={cap || `artifact ${i + 1}`} loading="lazy" className="w-full h-auto block" />
                  {cap && <figcaption className="text-[10px] text-[#94A3B8] px-2 py-1">{cap}</figcaption>}
                </figure>
              );
            })}
          </div>
        )}

        {/* Files / downloadable artifacts */}
        {files.length > 0 && (
          <div className="space-y-1.5">
            {files.map((f, i) => {
              const href = (f.url ?? f.href) as string;
              const name = f.name ?? f.filename ?? href.split('/').pop() ?? `file-${i + 1}`;
              const kind = (f.mime ?? f.content_type ?? '').split('/').pop()?.toUpperCase() ?? '';
              return (
                <a
                  key={i}
                  href={href}
                  target="_blank"
                  rel="noreferrer"
                  download={f.filename ?? f.name}
                  className="flex items-center gap-2.5 rounded-lg border border-[#1E2535] bg-[#0F1117] px-3 py-2 hover:border-[#2D3748] transition-colors group"
                >
                  <Paperclip className="h-3.5 w-3.5 text-[#64748B] shrink-0" aria-hidden />
                  <span className="flex-1 min-w-0 text-[12px] text-[#CBD5E1] truncate">{name}</span>
                  {kind && <span className="text-[9px] text-[#475569] font-mono">{kind}</span>}
                  {typeof f.size === 'number' && <span className="text-[9px] text-[#475569]">{fmtBytes(f.size)}</span>}
                  <Download className="h-3.5 w-3.5 text-[#64748B] group-hover:text-[#94A3B8] shrink-0" aria-hidden />
                </a>
              );
            })}
          </div>
        )}

        {/* Links */}
        {links.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {links.map((l, i) => {
              const href = (typeof l === 'string' ? l : (l.url ?? l.href)) as string;
              const label = typeof l === 'string' ? href : (l.title ?? l.label ?? href);
              return (
                <a
                  key={i}
                  href={href}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 rounded-lg border border-[#1E2535] bg-[#0F1117] px-2 py-1 text-[11px] text-[#00D4FF] hover:border-[#00D4FF]/40 transition-colors max-w-full"
                >
                  <LinkIcon className="h-3 w-3 shrink-0" aria-hidden />
                  <span className="truncate">{label}</span>
                </a>
              );
            })}
          </div>
        )}

        {/* Evidence / verification */}
        {(ev.verification || (Array.isArray(ev.tools) && ev.tools.length > 0)) && (
          <div className="rounded-lg border border-[#1E2535] bg-[#0F1117] p-3 space-y-2">
            {ev.verification && (
              <div className="flex items-start gap-2">
                <ShieldCheck className="h-3.5 w-3.5 text-blue-400 shrink-0 mt-0.5" aria-hidden />
                <p className="text-[11px] text-[#94A3B8] leading-relaxed">{ev.verification}</p>
              </div>
            )}
            {Array.isArray(ev.tools) && ev.tools.length > 0 && (
              <div className="flex items-center gap-1.5 flex-wrap">
                <Wrench className="h-3 w-3 text-[#475569]" aria-hidden />
                {ev.tools.map((tool, i) => (
                  <span
                    key={i}
                    className="text-[10px] text-[#94A3B8] bg-[#1A1F2E] border border-[#1E2535] rounded px-1.5 py-0.5"
                  >
                    {typeof tool === 'string' ? tool : JSON.stringify(tool)}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        {/* If nothing readable was rendered, show the object directly */}
        {!body &&
          metrics.length === 0 &&
          tables.length === 0 &&
          files.length === 0 &&
          images.length === 0 &&
          links.length === 0 && (
          <pre className="text-[11px] text-[#94A3B8] bg-[#0B0E14] border border-[#1E2535] rounded-lg p-3 overflow-x-auto whitespace-pre-wrap break-words">
            {JSON.stringify(d, null, 2)}
          </pre>
        )}

        {/* Raw JSON toggle */}
        <button
          onClick={() => setShowRaw((v) => !v)}
          className="flex items-center gap-1 text-[10px] text-[#475569] hover:text-[#94A3B8] transition-colors"
          aria-expanded={showRaw}
        >
          <ChevronDown
            className={cn('h-3 w-3 transition-transform', showRaw && 'rotate-180')}
            aria-hidden
          />
          {showRaw ? 'Hide' : 'View'} raw JSON
        </button>
        {showRaw && (
          <pre className="text-[10px] text-[#94A3B8] bg-[#0B0E14] border border-[#1E2535] rounded-lg p-3 overflow-x-auto whitespace-pre-wrap break-words max-h-80">
            {JSON.stringify(raw, null, 2)}
          </pre>
        )}
      </div>
    </div>
  );
}

/** Render a table given either {columns, rows} or an array of row objects. */
function DeliverableTable({ table }: { table: TableLike }) {
  let columns: string[] = [];
  let rows: unknown[][] = [];
  let title = '';

  if (Array.isArray(table)) {
    const objs = table as Record<string, unknown>[];
    columns = objs.length ? Object.keys(objs[0]) : [];
    rows = objs.map((o) => columns.map((c) => o[c]));
  } else {
    const t = table as { title?: string; columns?: string[]; rows?: unknown[][] };
    title = t.title ?? '';
    columns = t.columns ?? [];
    rows = t.rows ?? [];
  }
  if (columns.length === 0 && rows.length === 0) return null;

  return (
    <div className="rounded-lg border border-[#1E2535] overflow-hidden">
      {title && (
        <p className="text-[10px] font-medium text-[#94A3B8] px-3 py-1.5 bg-[#1A1F2E] border-b border-[#1E2535]">
          {title}
        </p>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-[11px]">
          {columns.length > 0 && (
            <thead>
              <tr className="bg-[#0F1117]">
                {columns.map((c, i) => (
                  <th
                    key={i}
                    className="text-left font-medium text-[#94A3B8] px-3 py-1.5 border-b border-[#1E2535] whitespace-nowrap"
                  >
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
          )}
          <tbody>
            {rows.map((r, ri) => (
              <tr key={ri} className="border-b border-[#1E2535] last:border-0">
                {(Array.isArray(r) ? r : [r]).map((cell, ci) => (
                  <td key={ci} className="text-[#CBD5E1] px-3 py-1.5 align-top">
                    {typeof cell === 'object' ? JSON.stringify(cell) : String(cell ?? '')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Render each evidence item in its natural form — a link, a labelled value, or text. */
function EvidenceStrip({ evidence }: { evidence: unknown[] }) {
  return (
    <div className="rounded-lg border border-[#1E2535] bg-[#0F1117] p-3 space-y-1.5">
      <p className="text-[10px] font-medium uppercase tracking-wide text-[#475569]">
        Evidence · {evidence.length}
      </p>
      <ul className="space-y-1">
        {evidence.map((e, i) => {
          if (isUrl(e)) {
            return (
              <li key={i}>
                <a href={e} target="_blank" rel="noreferrer"
                  className="inline-flex items-center gap-1 text-[11px] text-[#00D4FF] hover:underline break-all">
                  <LinkIcon className="h-3 w-3 shrink-0" aria-hidden />{e}
                </a>
              </li>
            );
          }
          if (e && typeof e === 'object') {
            const o = e as Record<string, unknown>;
            const link = ['url', 'href', 'link'].map((k) => o[k]).find(isUrl);
            const label = (o.label ?? o.title ?? o.name ?? o.type ?? '') as string;
            const val = (o.value ?? o.detail ?? o.description ?? '') as unknown;
            return (
              <li key={i} className="text-[11px] text-[#94A3B8] flex items-start gap-1.5">
                <span className="text-[#475569] mt-px">›</span>
                <span className="min-w-0">
                  {label && <span className="text-[#CBD5E1] font-medium">{label}: </span>}
                  {link ? (
                    <a href={link} target="_blank" rel="noreferrer" className="text-[#00D4FF] hover:underline break-all">{link}</a>
                  ) : (
                    <span className="break-words">{typeof val === 'object' ? JSON.stringify(val) : String(val || JSON.stringify(o))}</span>
                  )}
                </span>
              </li>
            );
          }
          return (
            <li key={i} className="text-[11px] text-[#94A3B8] break-words">
              <span className="text-[#475569]">› </span>{String(e)}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
