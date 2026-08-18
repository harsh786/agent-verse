/**
 * BaseWorkflowNode — shared wrapper for all 14 workflow step node types.
 *
 * Provides:
 * - Consistent card layout with icon + label + status badge
 * - Input/output handles (React Flow)
 * - Running/failed/complete/HITL overlay
 * - Context menu (right-click)
 * - Focus indicator (WCAG 2.2 AA)
 * - Dark-mode compatible
 */
import { type MouseEvent } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { motion } from 'framer-motion';
import { CheckCircle, XCircle, Clock, Loader2, UserCheck, AlertTriangle } from 'lucide-react';
import { NODE_COLORS, NODE_ICONS, NODE_LABELS, type NodeType } from '../../design/tokens';
import { runningPulse } from '../../design/motion';

// ── Run status overlay ────────────────────────────────────────────────────────

const STATUS_OVERLAYS = {
  running:      { icon: <Loader2 className="h-3.5 w-3.5 animate-spin" />, cls: 'text-sky-400' },
  complete:     { icon: <CheckCircle className="h-3.5 w-3.5" />, cls: 'text-emerald-400' },
  failed:       { icon: <XCircle className="h-3.5 w-3.5" />, cls: 'text-red-400' },
  waiting_hitl: { icon: <UserCheck className="h-3.5 w-3.5" />, cls: 'text-rose-400' },
  paused:       { icon: <Clock className="h-3.5 w-3.5" />, cls: 'text-amber-400' },
  error:        { icon: <AlertTriangle className="h-3.5 w-3.5" />, cls: 'text-orange-400' },
};

// ── Node data interface ───────────────────────────────────────────────────────

export interface WorkflowNodeData {
  label?: string;
  stepType: string;
  runStatus?: keyof typeof STATUS_OVERLAYS | null;
  selected?: boolean;
  hasError?: boolean;
  errorMessage?: string;
  onContextMenu?: (e: MouseEvent, nodeId: string) => void;
  [key: string]: unknown;
}

// ── Base node component ───────────────────────────────────────────────────────

export interface BaseNodeProps extends NodeProps {
  data: WorkflowNodeData;
}

export function BaseWorkflowNode({
  id,
  data,
  selected,
}: BaseNodeProps) {
  const stepType = data.stepType ?? 'tool';
  const colors = NODE_COLORS[stepType as NodeType] ?? {
    bg: 'bg-zinc-500/15', border: 'border-zinc-400/40', text: 'text-zinc-300',
  };
  const icon = NODE_ICONS[stepType] ?? '◻';
  const label = data.label ?? NODE_LABELS[stepType] ?? stepType;
  const runStatus = data.runStatus;
  const statusOverlay = runStatus ? STATUS_OVERLAYS[runStatus] : null;
  const isRunning = runStatus === 'running';

  return (
    <>
      {/* Input handle (left) */}
      <Handle
        type="target"
        position={Position.Left}
        className="!w-3 !h-3 !bg-[#0F1826]/20 !border-2 !border-white/40 hover:!bg-sky-400"
        aria-label={`Input to ${label}`}
      />

      {/* Node card */}
      <motion.div
        animate={isRunning ? 'animate' : 'initial'}
        variants={isRunning ? runningPulse : undefined}
        className={`
          relative min-w-[180px] max-w-[220px] rounded-xl border
          ${colors.bg} ${colors.border}
          ${selected ? 'ring-2 ring-sky-500 ring-offset-1 ring-offset-transparent' : ''}
          ${data.hasError ? 'ring-1 ring-red-500/60' : ''}
          shadow-lg shadow-black/30
          focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500
          transition-shadow
        `}
        role="button"
        tabIndex={0}
        aria-label={`Workflow step: ${label} (${stepType})`}
        aria-selected={selected}
        onContextMenu={(e) => data.onContextMenu?.(e, id)}
      >
        {/* Step type header */}
        <div className={`flex items-center gap-2 px-3 py-2 border-b border-white/10 ${colors.text}`}>
          <span className="text-sm" aria-hidden>{icon}</span>
          <span className="text-xs font-semibold uppercase tracking-wide opacity-80">
            {NODE_LABELS[stepType] ?? stepType}
          </span>
          {/* Run status badge */}
          {statusOverlay && (
            <span className={`ml-auto ${statusOverlay.cls}`} aria-label={`Status: ${runStatus}`}>
              {statusOverlay.icon}
            </span>
          )}
        </div>

        {/* Node body */}
        <div className="px-3 py-2.5">
          <p className="text-white text-sm font-medium truncate leading-tight">
            {label}
          </p>
          {typeof data.subtitle === 'string' && (
            <p className="text-white/40 text-xs mt-0.5 truncate">{data.subtitle}</p>
          )}
          {data.hasError && data.errorMessage && (
            <p className="text-red-400 text-xs mt-1 leading-tight line-clamp-2" role="alert">
              {String(data.errorMessage)}
            </p>
          )}
        </div>
      </motion.div>

      {/* Output handle (right) */}
      <Handle
        type="source"
        position={Position.Right}
        className="!w-3 !h-3 !bg-[#0F1826]/20 !border-2 !border-white/40 hover:!bg-sky-400"
        aria-label={`Output from ${label}`}
      />
    </>
  );
}
