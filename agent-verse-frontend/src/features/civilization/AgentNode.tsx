/**
 * AgentNode — world-class React Flow node for civilization agents.
 *
 * Design: Dark glassmorphic card with status-gradient left border,
 * reputation ring, animated status dot, role badge, cost display.
 */
import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';

interface AgentNodeData {
  label: string;
  status: string;
  reputation: number;
  depth: number;
  role?: string;
  current_step?: string;
  budget_spent_usd?: number;
}

// Status → gradient + glow colour tokens
const STATUS_CONFIG: Record<string, {
  gradient: string;
  glow: string;
  dot: string;
  badge: string;
  pulse: boolean;
}> = {
  active: {
    gradient: 'from-blue-500 to-indigo-500',
    glow: 'shadow-blue-500/30',
    dot: 'bg-blue-400',
    badge: 'bg-blue-500/20 text-blue-300 border-blue-500/30',
    pulse: true,
  },
  debating: {
    gradient: 'from-purple-500 to-violet-600',
    glow: 'shadow-purple-500/30',
    dot: 'bg-purple-400',
    badge: 'bg-purple-500/20 text-purple-300 border-purple-500/30',
    pulse: true,
  },
  spawning: {
    gradient: 'from-amber-400 to-orange-500',
    glow: 'shadow-amber-500/30',
    dot: 'bg-amber-400',
    badge: 'bg-amber-500/20 text-amber-300 border-amber-500/30',
    pulse: true,
  },
  idle: {
    gradient: 'from-slate-500 to-slate-600',
    glow: 'shadow-slate-500/20',
    dot: 'bg-slate-400',
    badge: 'bg-slate-500/20 text-slate-400 border-slate-500/30',
    pulse: false,
  },
  retired: {
    gradient: 'from-gray-600 to-gray-700',
    glow: 'shadow-gray-500/10',
    dot: 'bg-gray-500',
    badge: 'bg-gray-500/10 text-gray-500 border-gray-600/30',
    pulse: false,
  },
  failed: {
    gradient: 'from-red-500 to-rose-600',
    glow: 'shadow-red-500/30',
    dot: 'bg-red-400',
    badge: 'bg-red-500/20 text-red-300 border-red-500/30',
    pulse: false,
  },
};

const ROLE_ICONS: Record<string, string> = {
  worker: '⚙',
  analyst: '🔬',
  coordinator: '🎯',
  researcher: '📚',
  supervisor: '👁',
  default: '🤖',
};

const STATUS_LABELS: Record<string, string> = {
  active: 'Active',
  debating: 'Debating',
  spawning: 'Spawning',
  idle: 'Idle',
  retired: 'Retired',
  failed: 'Failed',
};

export const AgentNode = memo(({ data, selected }: { data: AgentNodeData; selected?: boolean }) => {
  const cfg = STATUS_CONFIG[data.status] ?? STATUS_CONFIG.idle;
  const repPct = Math.round((data.reputation ?? 0.5) * 100);
  const circumference = 2 * Math.PI * 18; // r=18
  const strokeDash = (repPct / 100) * circumference;
  const roleKey = (data.role ?? 'default').toLowerCase();
  const roleIcon = ROLE_ICONS[roleKey] ?? ROLE_ICONS.default;

  return (
    <div
      className={`
        relative rounded-2xl overflow-visible cursor-pointer select-none
        transition-all duration-200 ease-out
        ${selected
          ? `ring-2 ring-white/60 ring-offset-2 ring-offset-slate-900 shadow-2xl ${cfg.glow} scale-105`
          : `hover:ring-1 hover:ring-white/30 hover:shadow-xl hover:${cfg.glow} hover:scale-[1.02]`
        }
      `}
      style={{ minWidth: 164 }}
    >
      {/* Top handle */}
      <Handle
        type="target"
        position={Position.Top}
        className="!w-3 !h-3 !bg-slate-600 !border-2 !border-slate-400 !rounded-full"
      />

      {/* Card body */}
      <div
        className="rounded-2xl border border-white/10 overflow-hidden"
        style={{
          background: 'linear-gradient(135deg, rgba(15,23,42,0.95) 0%, rgba(30,41,59,0.90) 100%)',
          backdropFilter: 'blur(12px)',
          boxShadow: selected
            ? '0 0 0 1px rgba(255,255,255,0.15), 0 20px 40px rgba(0,0,0,0.5)'
            : '0 8px 24px rgba(0,0,0,0.4)',
        }}
      >
        {/* Gradient accent bar top */}
        <div className={`h-1 w-full bg-gradient-to-r ${cfg.gradient}`} />

        <div className="p-3">
          {/* Header row: avatar ring + name + role badge */}
          <div className="flex items-center gap-2.5 mb-2.5">
            {/* Reputation ring SVG */}
            <div className="relative flex-shrink-0">
              <svg width="40" height="40" className="-rotate-90">
                {/* Track */}
                <circle cx="20" cy="20" r="18" fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="3" />
                {/* Progress */}
                <circle
                  cx="20" cy="20" r="18"
                  fill="none"
                  stroke="url(#repGrad)"
                  strokeWidth="3"
                  strokeLinecap="round"
                  strokeDasharray={`${strokeDash} ${circumference}`}
                  style={{ transition: 'stroke-dasharray 0.6s ease' }}
                />
                <defs>
                  <linearGradient id="repGrad" x1="0%" y1="0%" x2="100%" y2="0%">
                    <stop offset="0%" stopColor={repPct > 60 ? '#22c55e' : repPct > 30 ? '#f59e0b' : '#ef4444'} />
                    <stop offset="100%" stopColor={repPct > 60 ? '#4ade80' : repPct > 30 ? '#fbbf24' : '#f87171'} />
                  </linearGradient>
                </defs>
              </svg>
              {/* Icon in centre */}
              <div
                className="absolute inset-0 flex items-center justify-center text-sm"
                style={{ filter: 'grayscale(0.3)' }}
              >
                {roleIcon}
              </div>
            </div>

            <div className="min-w-0 flex-1">
              <div className="font-semibold text-white text-sm leading-tight truncate" title={data.label}>
                {data.label}
              </div>
              {data.role && (
                <div className={`mt-0.5 inline-flex items-center text-[10px] font-medium px-1.5 py-px rounded-full border ${cfg.badge}`}>
                  {data.role}
                </div>
              )}
            </div>
          </div>

          {/* Status row */}
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-1.5">
              <span
                className={`w-2 h-2 rounded-full flex-shrink-0 ${cfg.dot} ${cfg.pulse ? 'animate-pulse' : ''}`}
                style={data.status === 'debating' ? { animation: 'ping 1s cubic-bezier(0,0,0.2,1) infinite' } : undefined}
              />
              <span className="text-xs text-slate-300 font-medium">
                {STATUS_LABELS[data.status] ?? data.status}
              </span>
            </div>
            <span className="text-[10px] text-slate-500 font-mono">D:{data.depth}</span>
          </div>

          {/* Reputation bar */}
          <div className="space-y-0.5">
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-slate-500">Reputation</span>
              <span className={`text-[10px] font-semibold tabular-nums ${
                repPct > 60 ? 'text-green-400' : repPct > 30 ? 'text-amber-400' : 'text-red-400'
              }`}>
                {repPct}%
              </span>
            </div>
            <div className="h-1 bg-white/5 rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full bg-gradient-to-r ${cfg.gradient} transition-all duration-700`}
                style={{ width: `${repPct}%` }}
              />
            </div>
          </div>

          {/* Cost + step */}
          {(data.budget_spent_usd !== undefined || data.current_step) && (
            <div className="mt-2 pt-2 border-t border-white/5 flex items-center justify-between gap-2">
              {data.budget_spent_usd !== undefined && (
                <span className="text-[10px] text-slate-500 font-mono">
                  ${data.budget_spent_usd.toFixed(3)}
                </span>
              )}
              {data.current_step && (
                <span className="text-[10px] text-slate-400 truncate flex-1 text-right" title={data.current_step}>
                  ▶ {data.current_step.slice(0, 22)}{data.current_step.length > 22 ? '…' : ''}
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Bottom handle */}
      <Handle
        type="source"
        position={Position.Bottom}
        className="!w-3 !h-3 !bg-slate-600 !border-2 !border-slate-400 !rounded-full"
      />
    </div>
  );
});

AgentNode.displayName = 'AgentNode';
