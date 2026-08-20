/**
 * ToolSparkNode — Tool call spark animation node.
 * Spec §3.5: Sparks radiate on tool call, green/red burst on result.
 */
import { useEffect, useState } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Zap } from 'lucide-react';
import { cn } from '@/lib/utils';

type ToolNodeState = 'idle' | 'calling' | 'success' | 'failed';

interface ToolSparkNodeProps {
  toolName:  string;
  state:     ToolNodeState;
  durationMs?: number;
  className?:  string;
}

const STATE_COLOR: Record<ToolNodeState, string> = {
  idle:    '#475569',
  calling: '#FFB300',
  success: '#00E676',
  failed:  '#FF3366',
};

export function ToolSparkNode({ toolName, state, className }: ToolSparkNodeProps) {
  const reduce = useReducedMotion();
  const [sparks, setSparks] = useState<{ id: number; angle: number }[]>([]);
  const color = STATE_COLOR[state];

  useEffect(() => {
    if (reduce || state === 'idle') return;
    const count = state === 'success' ? 8 : state === 'failed' ? 4 : 6;
    setSparks(Array.from({ length: count }, (_, i) => ({ id: i, angle: (i / count) * 360 })));
    const t = setTimeout(() => setSparks([]), 600);
    return () => clearTimeout(t);
  }, [state, reduce]);

  return (
    <motion.div
      className={cn('relative flex flex-col items-center gap-1', className)}
      animate={{ boxShadow: state !== 'idle' ? `0 0 12px ${color}` : 'none' }}
      transition={{ type: 'spring', stiffness: 300, damping: 28 }}
    >
      {/* Spark burst */}
      <AnimatePresence>
        {sparks.map(s => (
          <motion.div
            key={s.id}
            className="absolute w-1 h-1 rounded-full"
            style={{ background: color, top: '50%', left: '50%' }}
            initial={{ x: 0, y: 0, opacity: 1, scale: 1 }}
            animate={{
              x: Math.cos((s.angle * Math.PI) / 180) * 24,
              y: Math.sin((s.angle * Math.PI) / 180) * 24,
              opacity: 0, scale: 0,
            }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.5, ease: 'easeOut' }}
            aria-hidden
          />
        ))}
      </AnimatePresence>

      {/* Core node */}
      <motion.div
        className="rounded-lg px-2 py-1.5 flex items-center gap-1.5 border bg-[#0A0F1A]"
        style={{ borderColor: `${color}40` }}
        whileTap={{ scale: 0.96 }}
      >
        <Zap className="h-3 w-3" style={{ color }} aria-hidden />
        <span className="text-[10px] font-medium truncate max-w-[100px]" style={{ color }}>
          {toolName.slice(0, 30)}
        </span>
      </motion.div>
    </motion.div>
  );
}
