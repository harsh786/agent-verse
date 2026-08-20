/**
 * AgentConstellation — d3-force + React Flow agent constellation.
 * Spec §3.3: Living agent network with message beams, tool sparks, particle overlay.
 */
import { useRef, useState, useCallback, useEffect } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { AgentNeuralNode } from '@/components/neural/AgentNeuralNode';
import { ParticleCanvas, type ParticleCanvasRef } from '@/components/canvas/ParticleCanvas';
import { useConstellationLayout } from '@/hooks/useConstellationLayout';
import { cn } from '@/lib/utils';
import type { OrgMission } from '../types';

interface AgentConstellationProps {
  orgId:       string;
  missions:    OrgMission[];
  agents?:     Array<{ id: string; label: string; role?: string; status: 'active' | 'idle' | 'error'; goalCount: number }>;
  communicatingPairs?: [string, string][];
  onAgentSelect?:   (id: string | null) => void;
  onMissionSelect?: (id: string | null) => void;
  selectedAgentId?: string | null;
  className?:  string;
}

const CANVAS_W = 600;
const CANVAS_H = 480;

export function AgentConstellation({
  missions, agents = [], communicatingPairs = [],
  onAgentSelect, onMissionSelect, selectedAgentId, className,
}: AgentConstellationProps) {
  const reduce    = useReducedMotion();
  const canvasRef = useRef<ParticleCanvasRef>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [positions, setPositions] = useState<Map<string, { x: number; y: number }>>(new Map());

  // Build constellation nodes and edges
  const constellationNodes = [
    // CEO/hub node at center
    { id: '__hub__', type: 'agent' as const, isFixed: true, radius: 28 },
    ...agents.map(a => ({ id: a.id, type: 'agent' as const, radius: 20 + Math.min(a.goalCount, 4) * 3 })),
    ...missions.filter(m => m.status === 'active').slice(0, 8).map(m => ({ id: `mission-${m.id}`, type: 'mission' as const, radius: 14 })),
  ];

  const constellationEdges = [
    ...agents.map(a => ({ source: '__hub__', target: a.id, strength: 0.3 })),
    ...communicatingPairs.map(([s, t]) => ({ source: s, target: t, strength: 0.5 })),
  ];

  useConstellationLayout(constellationNodes, constellationEdges, {
    width: CANVAS_W, height: CANVAS_H,
    onTick: useCallback((pos: Map<string, { x: number; y: number }>) => {
      setPositions(new Map(pos));
    }, []),
  });

  // Emit burst when agent state changes to communicating
  useEffect(() => {
    for (const pair of communicatingPairs) {
      const pos = positions.get(pair[0]);
      if (pos) canvasRef.current?.emitBurst(pos, '#A855F7', 4);
    }
  }, [communicatingPairs.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const hubPos = positions.get('__hub__') ?? { x: CANVAS_W / 2, y: CANVAS_H / 2 };

  return (
    <div
      ref={containerRef}
      className={cn('relative bg-[#020408] rounded-xl overflow-hidden', className)}
      style={{ width: CANVAS_W, height: CANVAS_H }}
      aria-label="Agent constellation visualization"
    >
      {/* Background grid */}
      <svg className="absolute inset-0 pointer-events-none" width={CANVAS_W} height={CANVAS_H} aria-hidden>
        <defs>
          <pattern id="constellation-grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M 40 0 L 0 0 0 40" fill="none" stroke="rgba(255,255,255,0.03)" strokeWidth="0.5" />
          </pattern>
        </defs>
        <rect width={CANVAS_W} height={CANVAS_H} fill="url(#constellation-grid)" />

        {/* Edge lines */}
        {agents.map(a => {
          const aPos = positions.get(a.id);
          if (!aPos) return null;
          return (
            <line key={`edge-${a.id}`}
              x1={hubPos.x} y1={hubPos.y} x2={aPos.x} y2={aPos.y}
              stroke="rgba(255,255,255,0.06)" strokeWidth={1}
            />
          );
        })}

        {/* Communication beams */}
        {!reduce && communicatingPairs.map(([s, t], i) => {
          const sPos = positions.get(s);
          const tPos = positions.get(t);
          if (!sPos || !tPos) return null;
          return (
            <line key={`beam-${i}`}
              x1={sPos.x} y1={sPos.y} x2={tPos.x} y2={tPos.y}
              stroke="#A855F7" strokeWidth={1.5} strokeOpacity={0.5}
              style={{ filter: 'drop-shadow(0 0 4px #A855F7)' }}
            />
          );
        })}
      </svg>

      {/* Hub node */}
      <div className="absolute" style={{ left: hubPos.x - 28, top: hubPos.y - 28 }}>
        <motion.div
          className="w-14 h-14 rounded-full bg-[#0A0F1A] border-2 border-[#00D4FF]/60 flex items-center justify-center"
          style={{ boxShadow: '0 0 20px rgba(0,212,255,0.40)' }}
          animate={reduce ? {} : { scale: [1, 1.04, 1] }}
          transition={{ duration: 3, repeat: Infinity, ease: 'easeInOut' }}
          aria-label="Organization hub"
        >
          <span className="text-[#00D4FF] text-lg" aria-hidden>⚡</span>
        </motion.div>
      </div>

      {/* Agent nodes */}
      {agents.map(agent => {
        const pos = positions.get(agent.id);
        if (!pos) return null;
        return (
          <div
            key={agent.id}
            className="absolute"
            style={{ left: pos.x - 22, top: pos.y - 22 }}
          >
            <AgentNeuralNode
              agentId={agent.id}
              label={agent.label}
              role={agent.role}
              state={agent.status === 'active' ? 'executing' : agent.status === 'error' ? 'error' : 'idle'}
              isSelected={selectedAgentId === agent.id}
              onClick={onAgentSelect}
              size="md"
            />
          </div>
        );
      })}

      {/* Mission nodes */}
      {missions.filter(m => m.status === 'active').slice(0, 8).map(m => {
        const pos = positions.get(`mission-${m.id}`);
        if (!pos) return null;
        return (
          <button
            key={m.id}
            className="absolute rounded-lg px-2 py-1 bg-[#0A0F1A] border border-[#6366F1]/30 text-[9px] text-[#6366F1] font-medium max-w-[80px] truncate hover:border-[#6366F1]/60 transition-colors"
            style={{ left: pos.x - 40, top: pos.y - 12, boxShadow: '0 0 8px rgba(99,102,241,0.20)' }}
            onClick={() => onMissionSelect?.(m.id)}
            aria-label={`Mission: ${m.title}`}
          >
            {m.title.slice(0, 20)}
          </button>
        );
      })}

      {/* Particle canvas */}
      <ParticleCanvas ref={canvasRef} width={CANVAS_W} height={CANVAS_H} />
    </div>
  );
}
