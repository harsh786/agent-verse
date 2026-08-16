/**
 * WorkflowExecutionOverlay — live per-node status during workflow run.
 *
 * Animations:
 * - Status bar springs in from bottom
 * - Counter numbers animate with layout
 * - Running spinner pulses
 */
import { motion, AnimatePresence } from 'framer-motion';
import { Panel } from '@xyflow/react';
import { springs } from '../design/motion';

interface ExecutionOverlayProps {
  stepStatuses: Record<string, string>;
}

export function WorkflowExecutionOverlay({ stepStatuses }: ExecutionOverlayProps) {
  const runningCount = Object.values(stepStatuses).filter((s) => s === 'running').length;
  const completeCount = Object.values(stepStatuses).filter((s) => s === 'complete').length;
  const failedCount = Object.values(stepStatuses).filter((s) => s === 'failed').length;
  const total = Object.keys(stepStatuses).length;

  return (
    <Panel position="bottom-center" className="mb-4">
      <AnimatePresence>
        {total > 0 && (
          <motion.div
            initial={{ y: 40, opacity: 0, scale: 0.95 }}
            animate={{ y: 0, opacity: 1, scale: 1 }}
            exit={{ y: 40, opacity: 0, scale: 0.95 }}
            transition={springs.bouncy}
            className="flex items-center gap-4 bg-slate-900/90 backdrop-blur-md rounded-2xl
                         border border-white/10 px-5 py-2.5 shadow-xl text-sm"
            role="status"
            aria-label={`Execution status: ${completeCount}/${total} steps complete`}
          >
            {runningCount > 0 && (
              <motion.span
                layout
                className="flex items-center gap-1.5 text-sky-400"
              >
                <motion.span
                  animate={{ rotate: 360 }}
                  transition={{ repeat: Infinity, duration: 1, ease: 'linear' }}
                  aria-hidden
                  className="inline-block"
                >🔄</motion.span>
                {runningCount} running
              </motion.span>
            )}
            {completeCount > 0 && (
              <motion.span layout className="flex items-center gap-1.5 text-emerald-400">
                ✅ <motion.span key={completeCount} initial={{ scale: 1.3 }} animate={{ scale: 1 }} transition={springs.snappy}>{completeCount}/{total}</motion.span>
              </motion.span>
            )}
            {failedCount > 0 && (
              <motion.span
                layout
                initial={{ x: -4 }}
                animate={{ x: 0 }}
                className="flex items-center gap-1.5 text-red-400"
              >
                ❌ {failedCount} failed
              </motion.span>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </Panel>
  );
}
