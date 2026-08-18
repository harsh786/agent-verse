import { motion } from 'framer-motion';
import { springs } from '../design/motion';
/**
 * HITLContextRenderer — dispatches to the correct renderer by display_type.
 *
 * Supports all 7 context display types:
 * image | json | table | diff | chart | number | list
 */

export interface ContextItem {
  display_type: 'image' | 'json' | 'table' | 'diff' | 'chart' | 'number' | 'list';
  title: string;
  data: unknown;
  threshold_yellow?: number;
  threshold_red?: number;
  unit?: string;
}

// ── Inline renderers (lightweight, no heavy deps) ─────────────────────────────

function JsonRenderer({ item }: { item: ContextItem }) {
  return (
    <div>
      <p className="text-xs text-white/40 font-semibold mb-1.5">{item.title}</p>
      <pre className="text-xs font-mono text-slate-300 bg-slate-900/60 rounded-lg p-3
                      overflow-auto max-h-40 leading-relaxed">
        {typeof item.data === 'string' ? item.data : JSON.stringify(item.data, null, 2)}
      </pre>
    </div>
  );
}

function TableRenderer({ item }: { item: ContextItem }) {
  const rows = Array.isArray(item.data) ? item.data as Record<string, unknown>[] : [];
  const headers = rows.length > 0 ? Object.keys(rows[0]) : [];

  return (
    <div>
      <p className="text-xs text-white/40 font-semibold mb-1.5">{item.title}</p>
      <div className="overflow-auto max-h-48 rounded-lg border border-white/10">
        <table className="w-full text-xs" role="table">
          <thead className="bg-[#0F1826]/5">
            <tr>
              {headers.map((h) => (
                <th key={h} className="px-3 py-2 text-left text-white/50 font-medium" scope="col">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-t border-white/5 hover:bg-white/3">
                {headers.map((h) => (
                  <td key={h} className="px-3 py-2 text-white/70">
                    {String(row[h] ?? '')}
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

function DiffRenderer({ item }: { item: ContextItem }) {
  const data = item.data as { before?: string; after?: string } | null;
  return (
    <div>
      <p className="text-xs text-white/40 font-semibold mb-1.5">{item.title}</p>
      <div className="grid grid-cols-2 gap-2 text-xs font-mono">
        <div className="rounded-lg bg-red-950/40 border border-red-500/20 p-2 overflow-auto max-h-32">
          <p className="text-red-400 font-semibold mb-1">Before</p>
          <pre className="text-red-300/80 leading-relaxed">{String(data?.before ?? '')}</pre>
        </div>
        <div className="rounded-lg bg-emerald-950/40 border border-emerald-500/20 p-2 overflow-auto max-h-32">
          <p className="text-emerald-400 font-semibold mb-1">After</p>
          <pre className="text-emerald-300/80 leading-relaxed">{String(data?.after ?? '')}</pre>
        </div>
      </div>
    </div>
  );
}

function NumberRenderer({ item }: { item: ContextItem }) {
  const data = item.data as { value: number; unit?: string } | number | null;
  const value = typeof data === 'number' ? data : (data as {value: number})?.value ?? 0;
  const unit = item.unit ?? (typeof data === 'object' && data !== null ? (data as {unit?: string}).unit : '') ?? '';

  const isRed = item.threshold_red != null && value >= item.threshold_red;
  const isYellow = !isRed && item.threshold_yellow != null && value >= item.threshold_yellow;
  const colorCls = isRed ? 'text-red-400' : isYellow ? 'text-amber-400' : 'text-emerald-400';

  return (
    <div>
      <p className="text-xs text-white/40 font-semibold mb-1.5">{item.title}</p>
      <div className={`text-3xl font-bold ${colorCls}`}>
        {typeof value === 'number' ? value.toLocaleString() : String(value)}
        {unit && <span className="text-lg ml-1 opacity-60">{unit}</span>}
      </div>
      {(item.threshold_yellow || item.threshold_red) && (
        <div className="flex gap-3 mt-2 text-xs text-white/30">
          {item.threshold_yellow && <span>⚠️ ≥{item.threshold_yellow}</span>}
          {item.threshold_red && <span>🔴 ≥{item.threshold_red}</span>}
        </div>
      )}
    </div>
  );
}

function ListRenderer({ item }: { item: ContextItem }) {
  const items = Array.isArray(item.data) ? item.data as unknown[] : [item.data];
  return (
    <div>
      <p className="text-xs text-white/40 font-semibold mb-1.5">{item.title}</p>
      <ul className="space-y-1" role="list">
        {items.map((it, i) => (
          <li key={i} className="flex items-start gap-2 text-xs text-white/70">
            <span className="text-white/30 mt-0.5" aria-hidden>•</span>
            <span>{typeof it === 'object' ? JSON.stringify(it) : String(it)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ImageRenderer({ item }: { item: ContextItem }) {
  const src = typeof item.data === 'string' ? item.data : String(item.data);
  return (
    <div>
      <p className="text-xs text-white/40 font-semibold mb-1.5">{item.title}</p>
      <div className="rounded-lg overflow-hidden border border-white/10 max-h-48">
        <img
          src={src}
          alt={item.title}
          className="w-full h-full object-contain bg-slate-900"
          loading="lazy"
        />
      </div>
    </div>
  );
}

function ChartRenderer({ item }: { item: ContextItem }) {
  // Simple text fallback — full Recharts integration in Phase 6
  return (
    <div>
      <p className="text-xs text-white/40 font-semibold mb-1.5">{item.title}</p>
      <div className="rounded-lg bg-[#0F1826]/3 border border-white/8 p-3 text-xs text-white/40 text-center">
        📊 Chart: {JSON.stringify(item.data).slice(0, 80)}…
      </div>
    </div>
  );
}

// ── Dispatcher ────────────────────────────────────────────────────────────────

const RENDERERS: Record<ContextItem['display_type'], React.FC<{ item: ContextItem }>> = {
  json:   JsonRenderer,
  table:  TableRenderer,
  diff:   DiffRenderer,
  number: NumberRenderer,
  list:   ListRenderer,
  image:  ImageRenderer,
  chart:  ChartRenderer,
};

export function HITLContextRenderer({ item }: { item: ContextItem }) {
  const Renderer = RENDERERS[item.display_type] ?? JsonRenderer;
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={springs.gentle}
      className="rounded-xl border border-white/8 bg-[#0F1826]/3 p-4"
      role="region"
      aria-label={`Context: ${item.title} (${item.display_type})`}
    >
      <Renderer item={item} />
    </motion.div>
  );
}

export function HITLContextList({ items }: { items: ContextItem[] }) {
  if (!items?.length) return null;
  return (
    <div className="space-y-3">
      {items.map((item, i) => (
        <HITLContextRenderer key={i} item={item} />
      ))}
    </div>
  );
}
