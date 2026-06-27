/**
 * Custom React Flow node for civilization agents.
 * Color by status, ring thickness by reputation.
 */
import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';

interface AgentNodeData {
  label: string;
  status: string;
  reputation: number;
  depth: number;
  current_step?: string;
  budget_spent_usd?: number;
}

const STATUS_COLORS: Record<string, string> = {
  spawning: 'bg-amber-100 border-amber-400 text-amber-800',
  active: 'bg-blue-100 border-blue-400 text-blue-800',
  debating: 'bg-purple-100 border-purple-400 text-purple-800',
  idle: 'bg-slate-100 border-slate-400 text-slate-600',
  retired: 'bg-gray-100 border-gray-300 text-gray-400',
  failed: 'bg-red-100 border-red-400 text-red-700',
};

export const AgentNode = memo(({ data, selected }: { data: AgentNodeData; selected?: boolean }) => {
  const colorClass = STATUS_COLORS[data.status] ?? STATUS_COLORS.idle;
  const ringWidth = Math.max(2, Math.round(data.reputation * 6)); // 2-6px ring

  return (
    <div
      className={`rounded-lg border-2 p-3 min-w-[140px] max-w-[180px] text-xs shadow-sm transition-all ${colorClass} ${
        selected ? 'ring-2 ring-offset-1 ring-blue-500' : ''
      }`}
      style={{ borderWidth: ringWidth }}
    >
      <Handle type="target" position={Position.Top} className="!w-2 !h-2" />

      <div className="font-semibold truncate" title={data.label}>
        {data.label}
      </div>

      <div className="mt-1 flex items-center gap-1">
        <span className={`inline-block w-1.5 h-1.5 rounded-full ${
          data.status === 'active' ? 'bg-blue-500 animate-pulse' :
          data.status === 'debating' ? 'bg-purple-500 animate-ping' :
          data.status === 'spawning' ? 'bg-amber-400' :
          data.status === 'failed' ? 'bg-red-500' : 'bg-slate-400'
        }`} />
        <span className="opacity-70 capitalize">{data.status}</span>
      </div>

      <div className="mt-1 flex justify-between opacity-60">
        <span>Rep: {(data.reputation * 100).toFixed(0)}%</span>
        <span>D:{data.depth}</span>
      </div>

      {data.current_step && (
        <div className="mt-1 truncate opacity-50 text-[10px]" title={data.current_step}>
          &#9656; {data.current_step}
        </div>
      )}

      <Handle type="source" position={Position.Bottom} className="!w-2 !h-2" />
    </div>
  );
});

AgentNode.displayName = 'AgentNode';
