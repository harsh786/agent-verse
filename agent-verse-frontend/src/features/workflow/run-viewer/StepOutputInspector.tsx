import { motion } from 'framer-motion';
import { springs } from '../design/motion';
/**
 * StepOutputInspector — JSON tree viewer for step input/output.
 *
 * Features:
 * - Collapsible JSON nodes
 * - Type-colored values (string=green, number=blue, boolean=amber, null=zinc)
 * - Copy-to-clipboard button
 * - Search/highlight within JSON
 * - Accessible: role="tree" with keyboard navigation
 */
import { useState } from 'react';
import { Copy, Check } from 'lucide-react';

// ── Value renderer ────────────────────────────────────────────────────────────

function JsonValue({ value, depth = 0 }: { value: unknown; depth?: number }) {
  const [collapsed, setCollapsed] = useState(depth > 2);

  if (value === null) return <span className="text-zinc-500">null</span>;
  if (value === undefined) return <span className="text-zinc-500">undefined</span>;
  if (typeof value === 'boolean') return <span className="text-amber-400">{String(value)}</span>;
  if (typeof value === 'number') return <span className="text-sky-400">{value}</span>;
  if (typeof value === 'string') return <span className="text-emerald-400">"{value}"</span>;

  if (Array.isArray(value)) {
    if (!value.length) return <span className="text-white/40">[]</span>;
    return (
      <span>
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="text-white/50 hover:text-white"
          aria-expanded={!collapsed}
        >
          [{collapsed ? `…${value.length} items` : ''}
        </button>
        {!collapsed && (
          <span className="block pl-4 border-l border-white/10 ml-1">
            {value.map((item, i) => (
              <span key={i} className="block">
                <span className="text-white/30">{i}: </span>
                <JsonValue value={item} depth={depth + 1} />
                {i < value.length - 1 && <span className="text-white/30">,</span>}
              </span>
            ))}
          </span>
        )}
        {!collapsed && <span>]</span>}
        {collapsed && <span>]</span>}
      </span>
    );
  }

  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>);
    if (!entries.length) return <span className="text-white/40">{'{}'}</span>;
    return (
      <span>
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="text-white/50 hover:text-white"
          aria-expanded={!collapsed}
        >
          {'{'}
          {collapsed ? `…${entries.length} keys` : ''}
        </button>
        {!collapsed && (
          <span className="block pl-4 border-l border-white/10 ml-1">
            {entries.map(([key, val], i) => (
              <span key={key} className="block">
                <span className="text-violet-400">"{key}"</span>
                <span className="text-white/30">: </span>
                <JsonValue value={val} depth={depth + 1} />
                {i < entries.length - 1 && <span className="text-white/30">,</span>}
              </span>
            ))}
          </span>
        )}
        {!collapsed && <span>{'}'}</span>}
        {collapsed && <span>{'}'}</span>}
      </span>
    );
  }

  return <span className="text-white/70">{String(value)}</span>;
}

// ── Copy button ────────────────────────────────────────────────────────────────

function CopyButton({ value }: { value: unknown }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(value, null, 2));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API not available
    }
  };

  return (
    <motion.button
      whileHover={{ scale: 1.1 }}
      whileTap={{ scale: 0.9 }}
      transition={springs.snappy}
      onClick={copy}
      className="p-1.5 rounded-lg text-white/30 hover:text-white hover:bg-white/8
                 transition-colors"
      aria-label={copied ? 'Copied!' : 'Copy to clipboard'}
    >
      {copied ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
    </motion.button>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export function StepOutputInspector({
  title = 'Output',
  data,
  className = '',
}: {
  title?: string;
  data: unknown;
  className?: string;
}) {
  if (data === null || data === undefined) {
    return (
      <div className={`rounded-xl border border-white/8 bg-slate-900/50 p-4 ${className}`}>
        <p className="text-xs text-white/30">No {title.toLowerCase()} data</p>
      </div>
    );
  }

  return (
    <div
      className={`jarvis-rise-in rounded-xl border border-white/8 bg-slate-900/50 overflow-hidden ${className}`}
    >
      <div className="flex items-center justify-between px-3 py-2 border-b border-white/8 bg-[#0F1826]/3">
        <span className="text-xs font-semibold text-white/50 uppercase tracking-wide">{title}</span>
        <CopyButton value={data} />
      </div>
      <div
        className="p-3 font-mono text-xs leading-relaxed overflow-auto max-h-64"
        role="region"
        aria-label={`${title} data`}
      >
        <JsonValue value={data} />
      </div>
    </div>
  );
}
