/**
 * TaskHandoffBeam — WS-7 item 2. Renders one in-flight task handoff: a
 * dashed SVG beam (SMIL, consistent with AgentConstellation's existing
 * comm/work beams) plus a labeled packet that travels along it with a
 * fading trail. Purely presentational — driven entirely by the positions
 * and label it's handed, which come from a real `TaskHandoff` computed by
 * `computeTaskHandoffs` off the live task feed.
 */
import { motion } from 'framer-motion';

export interface TaskHandoffBeamPoint { x: number; y: number }

interface TaskHandoffBeamProps {
  from:        TaskHandoffBeamPoint;
  to:          TaskHandoffBeamPoint;
  label:       string;
  durationMs?: number;
  reduce?:     boolean;
}

/** The dashed edge itself — rendered inside AgentConstellation's existing <svg>. */
export function TaskHandoffLine({ from, to, durationMs = 1400 }: Omit<TaskHandoffBeamProps, 'label' | 'reduce'>) {
  return (
    <line
      x1={from.x} y1={from.y} x2={to.x} y2={to.y}
      stroke="#00E676" strokeWidth={1.5} strokeOpacity={0.5}
      strokeDasharray="4 8" strokeLinecap="round"
      style={{ filter: 'drop-shadow(0 0 4px rgba(0,230,118,0.85))' }}
    >
      <animate
        attributeName="stroke-dashoffset"
        from="24" to="0" dur={`${(durationMs / 1000).toFixed(2)}s`} repeatCount="1"
      />
    </line>
  );
}

/** The traveling, labeled packet — rendered as an absolutely-positioned HTML
 *  overlay alongside AgentConstellation's other absolute-positioned nodes. */
export function TaskHandoffLabel({ from, to, label, durationMs = 1400, reduce }: TaskHandoffBeamProps) {
  if (reduce) return null; // reduced-motion: the static line above is enough
  const duration = durationMs / 1000;
  return (
    <motion.div
      className="absolute pointer-events-none z-10 px-1.5 py-0.5 rounded bg-[#00E676]/15 border border-[#00E676]/40 text-[9px] text-[#00E676] font-medium whitespace-nowrap"
      style={{ transform: 'translate(-50%, -50%)' }}
      initial={{ left: from.x, top: from.y, opacity: 0 }}
      animate={{ left: to.x, top: to.y, opacity: [0, 1, 1, 0] }}
      transition={{ duration, ease: 'easeInOut', times: [0, 0.12, 0.75, 1] }}
      aria-hidden
    >
      {label}
    </motion.div>
  );
}
