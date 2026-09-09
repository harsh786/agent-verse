/**
 * AgentSpawnNode — WS-7 item 1 (agent spawn animation).
 *
 * Wraps one AgentConstellation node. Because React only mounts this
 * component the first time an agent id shows up in the live `agents` list
 * (which only happens on a real `org.agent.activated`-family SSE event, via
 * useOrgNeuralState's reducer), the mount lifecycle itself is the real
 * "spawn" signal — no timer, no fabricated activity. On that one mount we:
 *   1. play a scale+fade+blur entrance (the shared `nodeAppear` variant), and
 *   2. call `onSpawn` once so the caller can fire a particle burst at the
 *      node's (already-known) position.
 * Both collapse to an instant, burst-free appearance under
 * prefers-reduced-motion.
 */
import { useEffect, useRef } from 'react';
import { motion } from 'framer-motion';
import { nodeAppear, SPRING_NODE } from '@/lib/design/motion';

interface AgentSpawnNodePoint { x: number; y: number }

interface AgentSpawnNodeProps {
  pos:            AgentSpawnNodePoint;
  reduce?:        boolean;
  /** Stagger delay (seconds) so many agents spawning together cascade in
   *  instead of popping simultaneously — WS-7 item 5 (motion polish). */
  delay?:         number;
  onSpawn?:       (pos: AgentSpawnNodePoint) => void;
  style?:         React.CSSProperties;
  className?:     string;
  children:       React.ReactNode;
}

export function AgentSpawnNode({
  pos, reduce, delay = 0, onSpawn, style, className, children,
}: AgentSpawnNodeProps) {
  const firedRef = useRef(false);

  useEffect(() => {
    if (firedRef.current) return;
    firedRef.current = true;
    if (!reduce) onSpawn?.(pos);
    // Fire exactly once, at mount — a genuine spawn, never on re-render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <motion.div
      className={className}
      style={style}
      variants={nodeAppear}
      initial={reduce ? false : 'hidden'}
      animate="visible"
      transition={reduce ? { duration: 0 } : { ...SPRING_NODE, delay }}
    >
      {children}
    </motion.div>
  );
}
