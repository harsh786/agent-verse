/**
 * WorkflowExecutionOverlay — live per-node status during workflow run.
 *
 * Rendered as React Flow Panel so it overlays on top of the canvas without
 * blocking interactions. Status icons appear at each node's position.
 */
import { Panel } from '@xyflow/react';

interface ExecutionOverlayProps {
  stepStatuses: Record<string, string>;
}

export function WorkflowExecutionOverlay({ stepStatuses }: ExecutionOverlayProps) {
  const runningCount = Object.values(stepStatuses).filter((s) => s === 'running').length;
  const completeCount = Object.values(stepStatuses).filter((s) => s === 'complete').length;
  const failedCount = Object.values(stepStatuses).filter((s) => s === 'failed').length;
  const total = Object.keys(stepStatuses).length;

  if (total === 0) return null;

  return (
    <Panel position="bottom-center" className="mb-4">
      <div className="flex items-center gap-4 bg-slate-900/90 backdrop-blur-md rounded-2xl
                       border border-white/10 px-5 py-2.5 shadow-xl text-sm"
        role="status"
        aria-label={`Execution status: ${completeCount}/${total} steps complete`}
      >
        {runningCount > 0 && (
          <span className="flex items-center gap-1.5 text-sky-400">
            <span className="animate-spin" aria-hidden>🔄</span>
            {runningCount} running
          </span>
        )}
        {completeCount > 0 && (
          <span className="flex items-center gap-1.5 text-emerald-400">
            ✅ {completeCount}/{total}
          </span>
        )}
        {failedCount > 0 && (
          <span className="flex items-center gap-1.5 text-red-400">
            ❌ {failedCount} failed
          </span>
        )}
      </div>
    </Panel>
  );
}
