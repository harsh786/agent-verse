/**
 * GraphifyProgress — animated knowledge graph construction visualization.
 * The signature JARVIS feature: watch as the AI builds a knowledge graph
 * from your organization's data in real time.
 *
 * Skills:
 *   - frontend-design:   JARVIS dark, animated nodes pulsing, scanline depth
 *   - emil-design-eng:   spring enter/exit, stagger nodes appearing, breathing
 *   - impeccable-ui:     phase as dominant element, count as secondary
 *   - web-guidelines:    aria-live progress, role=status, tabular-nums
 *   - ui-ux-pro-max:     reduced-motion, 44px target, accessible progress
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { Network, Zap, CheckCircle2, AlertCircle, X, GitBranch, Brain, Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';

export type GraphifyPhase =
  | 'idle' | 'queued' | 'extracting' | 'building'
  | 'community' | 'discovery' | 'complete' | 'error';

export interface GraphifyProgressProps {
  orgId:     string;
  onClose?:  () => void;
  onComplete?: (stats: GraphifyStats) => void;
}

export interface GraphifyStats {
  nodes:       number;
  edges:       number;
  communities: number;
  discoveries: number;
}

interface SSEEvent {
  phase:       GraphifyPhase;
  progress?:   number;
  nodes?:      number;
  edges?:      number;
  communities?: number;
  discoveries?: number;
  message?:    string;
  error?:      string;
}

// ─── Phase config ────────────────────────────────────────────────────────────

const PHASE_CONFIG: Record<GraphifyPhase, { label: string; icon: typeof Zap; color: string; desc: string }> = {
  idle:        { label: 'Ready',            icon: Network,       color: 'text-[#475569]', desc: 'Waiting to start' },
  queued:      { label: 'Queued',           icon: Loader2,       color: 'text-amber-400', desc: 'Waiting in queue…' },
  extracting:  { label: 'Extracting',       icon: Brain,         color: 'text-blue-400',  desc: 'Scanning org data for entities' },
  building:    { label: 'Building Graph',   icon: GitBranch,     color: 'text-violet-400',desc: 'Connecting relationships' },
  community:   { label: 'Detecting',        icon: Network,       color: 'text-cyan-400',  desc: 'Finding knowledge clusters' },
  discovery:   { label: 'Discovering',      icon: Zap,           color: 'text-emerald-400',desc: 'Surfacing hidden patterns' },
  complete:    { label: 'Complete',         icon: CheckCircle2,  color: 'text-emerald-400',desc: 'Knowledge graph ready' },
  error:       { label: 'Failed',           icon: AlertCircle,   color: 'text-rose-400',  desc: 'Something went wrong' },
};

const PHASE_ORDER: GraphifyPhase[] = ['queued', 'extracting', 'building', 'community', 'discovery', 'complete'];

// ─── Spring configs ───────────────────────────────────────────────────────────

const PANEL_SPRING  = { type: 'spring', stiffness: 280, damping: 26 } as const;
const NODE_SPRING   = { type: 'spring', stiffness: 400, damping: 30 } as const;
const COUNT_SPRING  = { type: 'spring', stiffness: 200, damping: 28 } as const;

// ─── Component ────────────────────────────────────────────────────────────────

export function GraphifyProgress({ orgId, onClose, onComplete }: GraphifyProgressProps) {
  const reduce = useReducedMotion();
  const [phase, setPhase]       = useState<GraphifyPhase>('idle');
  const [progress, setProgress] = useState(0);
  const [stats, setStats]       = useState<GraphifyStats>({ nodes: 0, edges: 0, communities: 0, discoveries: 0 });
  const [message, setMessage]   = useState('');
  const [jobId, setJobId]       = useState<string | null>(null);
  const [error, setError]       = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  const start = useCallback(async () => {
    setPhase('queued');
    setError(null);
    setStats({ nodes: 0, edges: 0, communities: 0, discoveries: 0 });
    setProgress(0);
    try {
      const resp = await fetch(`/api/v1/org/${orgId}/graphify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
      });
      if (!resp.ok) throw new Error(`Failed to start: ${resp.status}`);
      const data = await resp.json() as { job_id: string };
      setJobId(data.job_id);
    } catch (err) {
      setPhase('error');
      setError(String(err));
    }
  }, [orgId]);

  // Connect SSE when jobId is set
  useEffect(() => {
    if (!jobId) return;
    const url = `/api/v1/org/${orgId}/graphify/${jobId}/stream`;
    const es = new EventSource(url, { withCredentials: true });
    esRef.current = es;

    es.onmessage = (e) => {
      try {
        const ev: SSEEvent = JSON.parse(e.data);
        setPhase(ev.phase);
        if (ev.progress != null) setProgress(ev.progress);
        if (ev.message)          setMessage(ev.message);
        if (ev.nodes != null || ev.edges != null) {
          setStats(prev => ({
            nodes:       ev.nodes       ?? prev.nodes,
            edges:       ev.edges       ?? prev.edges,
            communities: ev.communities ?? prev.communities,
            discoveries: ev.discoveries ?? prev.discoveries,
          }));
        }
        if (ev.phase === 'complete') {
          es.close();
          onComplete?.({ nodes: ev.nodes ?? 0, edges: ev.edges ?? 0, communities: ev.communities ?? 0, discoveries: ev.discoveries ?? 0 });
        }
        if (ev.phase === 'error') {
          setError(ev.error ?? 'Unknown error');
          es.close();
        }
      } catch {}
    };
    es.onerror = () => { setPhase('error'); setError('Stream disconnected'); es.close(); };
    return () => es.close();
  }, [jobId, orgId, onComplete]);

  const cfg  = PHASE_CONFIG[phase];
  const Icon = cfg.icon;
  const isRunning = !['idle', 'complete', 'error'].includes(phase);
  const phaseIdx  = PHASE_ORDER.indexOf(phase);

  return (
    <motion.div
      initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.97, y: 12 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.97, y: 8 }}
      transition={reduce ? { duration: 0.15 } : PANEL_SPRING}
      role="status"
      aria-label={`Graphify: ${cfg.label} — ${cfg.desc}`}
      aria-live="polite"
      className={cn(
        'rounded-2xl border bg-[#1A1F2E] border-[#2D3748]',
        'shadow-[0_8px_40px_rgba(0,0,0,0.5)]',
        'overflow-hidden',
      )}
    >
      {/* ── Header ───────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3 px-5 py-4 border-b border-[#2D3748]">
        <div className="h-8 w-8 rounded-lg bg-blue-500/10 border border-blue-500/20 flex items-center justify-center">
          <Network className="h-4 w-4 text-blue-400" aria-hidden />
        </div>
        <div className="flex-1 min-w-0">
          {/* impeccable-ui: label is the ONE dominant element */}
          <p className="text-[14px] font-semibold text-[#F1F5F9] tracking-[-0.01em]">
            Knowledge Graph
          </p>
          <p className="text-[11px] text-[#475569]">Graphify — AI-powered org analysis</p>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            aria-label="Close graphify panel"
            style={{ touchAction: 'manipulation' }}
            className="p-1.5 rounded-lg text-[#475569] hover:text-[#94A3B8] hover:bg-[#252B3B] transition-colors min-w-[36px] min-h-[36px] flex items-center justify-center"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        )}
      </div>

      {/* ── Phase indicator (JARVIS signature) ──────────────────────────── */}
      <div className="px-5 py-4 space-y-4">
        {/* Current phase */}
        <div className="flex items-center gap-3">
          <div className={cn('relative flex items-center justify-center h-10 w-10 rounded-xl shrink-0', isRunning ? 'bg-blue-500/10' : 'bg-[#252B3B]')}>
            {isRunning && !reduce && (
              <motion.div
                className="absolute inset-0 rounded-xl bg-blue-500/10"
                animate={{ scale: [1, 1.3, 1], opacity: [0.6, 0, 0.6] }}
                transition={{ duration: 2, repeat: Infinity }}
              />
            )}
            <motion.div
              animate={isRunning && !reduce ? { rotate: phase === 'extracting' ? 360 : 0 } : {}}
              transition={{ duration: 2, repeat: Infinity, ease: 'linear' }}
            >
              <Icon className={cn('h-5 w-5 relative z-10', cfg.color)} aria-hidden />
            </motion.div>
          </div>
          <div className="flex-1 min-w-0">
            <motion.p
              key={phase}
              initial={reduce ? {} : { opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              transition={NODE_SPRING}
              className={cn('text-[15px] font-semibold tracking-[-0.01em]', cfg.color)}
            >
              {cfg.label}
            </motion.p>
            <motion.p
              key={message || cfg.desc}
              initial={reduce ? {} : { opacity: 0 }}
              animate={{ opacity: 1 }}
              className="text-[12px] text-[#475569] truncate"
            >
              {message || cfg.desc}
            </motion.p>
          </div>
        </div>

        {/* Progress bar */}
        {isRunning && (
          <div>
            <div
              className="h-1.5 rounded-full bg-[#252B3B] overflow-hidden"
              role="progressbar"
              aria-valuenow={progress}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <motion.div
                className="h-full rounded-full bg-gradient-to-r from-blue-600 via-violet-500 to-cyan-500"
                initial={{ width: 0 }}
                animate={{ width: `${progress}%` }}
                transition={reduce ? { duration: 0 } : COUNT_SPRING}
              />
            </div>
            <p className="text-[11px] text-[#475569] mt-1 tabular-nums text-right">
              {progress}%
            </p>
          </div>
        )}

        {/* Phase steps pipeline */}
        <div className="flex items-center gap-1">
          {PHASE_ORDER.filter(p => p !== 'complete').map((p, i) => {
            const done    = phaseIdx > i;
            const active  = phaseIdx === i;
            return (
              <div key={p} className="flex items-center gap-1 flex-1">
                <motion.div
                  animate={reduce ? {} : active ? { scale: [1, 1.2, 1] } : {}}
                  transition={{ duration: 1.5, repeat: Infinity }}
                  className={cn(
                    'h-1.5 flex-1 rounded-full transition-colors duration-300',
                    done   ? 'bg-blue-500' :
                    active ? 'bg-blue-500/60' : 'bg-[#252B3B]',
                  )}
                />
              </div>
            );
          })}
        </div>

        {/* Stats (tabular-nums per web-guidelines) */}
        <div className="grid grid-cols-4 gap-2">
          {([ ['Nodes', stats.nodes], ['Edges', stats.edges], ['Clusters', stats.communities], ['Insights', stats.discoveries] ] as [string, number][]).map(([label, val]) => (
            <StatTile key={label} label={label} value={val} reduce={!!reduce} />
          ))}
        </div>

        {/* Actions */}
        {phase === 'idle' && (
          <button
            onClick={start}
            style={{ touchAction: 'manipulation' }}
            className={cn(
              'w-full py-2.5 rounded-xl text-[14px] font-semibold text-white',
              'bg-gradient-to-r from-blue-600 to-violet-600',
              'hover:from-blue-500 hover:to-violet-500',
              'active:scale-[0.98] transition-[transform,filter] duration-150',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500',
            )}
            aria-label="Start building knowledge graph"
          >
            Build Knowledge Graph
          </button>
        )}

        {phase === 'error' && (
          <div className="space-y-2">
            <p className="text-[12px] text-rose-400 bg-rose-500/10 px-3 py-2 rounded-lg" role="alert">
              {error ?? 'Failed to build graph'}
            </p>
            <button
              onClick={start}
              style={{ touchAction: 'manipulation' }}
              className="w-full py-2 rounded-lg text-[13px] font-medium text-rose-400 bg-rose-500/10 hover:bg-rose-500/20 transition-colors"
            >
              Retry
            </button>
          </div>
        )}

        {phase === 'complete' && (
          <motion.div
            initial={reduce ? {} : { opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ type: 'spring', stiffness: 400, damping: 25 }}
            className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/20 rounded-xl px-3 py-2"
          >
            <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0" aria-hidden />
            <p className="text-[13px] text-emerald-300 font-medium">
              Graph built — {stats.nodes} nodes, {stats.edges} edges
            </p>
          </motion.div>
        )}
      </div>
    </motion.div>
  );
}

// ─── Stat tile ────────────────────────────────────────────────────────────────

function StatTile({ label, value, reduce }: { label: string; value: number; reduce: boolean }) {
  return (
    <div className="flex flex-col items-center gap-0.5 bg-[#252B3B] rounded-lg py-2">
      <motion.span
        key={value}
        initial={reduce ? {} : { scale: 1.3, color: '#3B82F6' }}
        animate={{ scale: 1, color: '#F1F5F9' }}
        transition={{ type: 'spring', stiffness: 400, damping: 25 }}
        className="text-[15px] font-bold text-[#F1F5F9] tabular-nums"
      >
        {value.toLocaleString()}
      </motion.span>
      <span className="text-[10px] text-[#475569] uppercase tracking-[0.06em]">
        {label}
      </span>
    </div>
  );
}
