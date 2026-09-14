/**
 * AgentConstellation — d3-force + React Flow agent constellation.
 * Spec §3.3: Living agent network with message beams, tool sparks, particle overlay.
 */
import { useRef, useState, useCallback, useEffect } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { AgentNeuralNode } from '@/components/neural/AgentNeuralNode';
import { ParticleCanvas, type ParticleCanvasRef } from '@/components/canvas/ParticleCanvas';
import { useConstellationLayout } from '@/hooks/useConstellationLayout';
import { AgentSpawnNode } from './AgentSpawnNode';
import { TaskHandoffLine, TaskHandoffLabel } from './TaskHandoffBeam';
import { useTaskHandoffAnimations, HANDOFF_ANIMATION_MS } from '../hooks/useTaskHandoffAnimations';
import { cn } from '@/lib/utils';
import type { OrgMission } from '../types';
import type { OrgRecentMessage } from '../hooks/useOrgNeuralState';

interface AgentConstellationProps {
  orgId:       string;
  missions:    OrgMission[];
  agents?:     Array<{ id: string; label: string; role?: string; status: 'active' | 'idle' | 'error'; goalCount: number }>;
  communicatingPairs?: [string, string][];
  /** Recent collaboration messages (newest-first) — used to color/label each
   *  comm beam by the kind of the most recent message for that pair. Pairs
   *  with no matching message (e.g. from org.team.formed) keep the default color. */
  recentMessages?: OrgRecentMessage[];
  onAgentSelect?:   (id: string | null) => void;
  onMissionSelect?: (id: string | null) => void;
  /** Called with the most recent message id for a beam when it's clicked. */
  onBeamSelect?:    (messageId: string) => void;
  selectedAgentId?: string | null;
  className?:  string;
}

const CANVAS_W = 600;
const CANVAS_H = 480;

/** Design-system colors per collaboration message kind (Task 9 brief). */
const KIND_BEAM_COLORS: Record<string, string> = {
  update:   '#64748B',
  proposal: '#6366F1',
  question: '#00D4FF',
  handoff:  '#A855F7',
  result:   '#10B981',
  risk:     '#F59E0B',
  block:    '#EF4444',
};
const DEFAULT_BEAM_COLOR = '#A855F7';

/** Most recent message (recentMessages is newest-first) between an unordered pair. */
function latestMessageForPair(
  recentMessages: OrgRecentMessage[], a: string, b: string,
): OrgRecentMessage | undefined {
  return recentMessages.find(m => (m.from === a && m.to === b) || (m.from === b && m.to === a));
}

export function AgentConstellation({
  orgId, missions, agents = [], communicatingPairs = [], recentMessages = [],
  onAgentSelect, onMissionSelect, onBeamSelect, selectedAgentId, className,
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

  // Comm beams are deliberately NOT fed into the force layout as edges: they
  // fire on every live collaboration message, and pulling them into d3-force
  // would restart/perturb the whole simulation on each message. Beams are
  // rendered purely as an SVG overlay against positions the layout already
  // computed from the stable agent/hub/mission graph below.
  const constellationEdges = [
    ...agents.map(a => ({ source: '__hub__', target: a.id, strength: 0.3 })),
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

  // WS-7 item 2 — task-handoff animation. Driven by real task completions
  // (see useTaskHandoffAnimations / computeTaskHandoffs), never a timer.
  const handoffs = useTaskHandoffAnimations(orgId);
  const resolvedHandoffs = handoffs
    .map(h => ({ h, from: positions.get(h.fromAgentId), to: positions.get(h.toAgentId) }))
    .filter((r): r is { h: typeof handoffs[number]; from: { x: number; y: number }; to: { x: number; y: number } } =>
      !!r.from && !!r.to);

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

        {/* Edge lines — a static link, plus a flowing dashed overlay for agents
            that are actively working, so you can see tasks streaming out to each
            bot along its edge (hub → agent direction). */}
        {agents.map(a => {
          const aPos = positions.get(a.id);
          if (!aPos) return null;
          const working = a.status === 'active';
          return (
            <g key={`edge-${a.id}`}>
              <line
                x1={hubPos.x} y1={hubPos.y} x2={aPos.x} y2={aPos.y}
                stroke="rgba(255,255,255,0.06)" strokeWidth={1}
              />
              {working && !reduce && (
                <line
                  x1={hubPos.x} y1={hubPos.y} x2={aPos.x} y2={aPos.y}
                  stroke="#00D4FF" strokeWidth={1.5} strokeOpacity={0.55}
                  strokeDasharray="2 11" strokeLinecap="round"
                  style={{ filter: 'drop-shadow(0 0 3px rgba(0,212,255,0.9))' }}
                >
                  {/* Decreasing dashoffset marches the dashes from the hub toward
                      the agent — reads as work/tasks flowing to the bot. */}
                  <animate
                    attributeName="stroke-dashoffset"
                    from="26" to="0" dur="0.9s" repeatCount="indefinite"
                  />
                </line>
              )}
            </g>
          );
        })}

        {/* Communication beams — colored by the kind of the most recent real
            collaboration message for that pair (default purple when the pair
            came from org.team.formed and has no message yet). */}
        {communicatingPairs.map(([s, t], i) => {
          const sPos = positions.get(s);
          const tPos = positions.get(t);
          if (!sPos || !tPos) return null;
          const msg = latestMessageForPair(recentMessages, s, t);
          const color = msg ? (KIND_BEAM_COLORS[msg.kind] ?? DEFAULT_BEAM_COLOR) : DEFAULT_BEAM_COLOR;
          const tooltip = msg ? `${msg.from} → ${msg.to} · ${msg.kind}` : `${s} → ${t}`;
          const clickable = !!(onBeamSelect && msg);
          const selectMsg = () => { if (msg && onBeamSelect) onBeamSelect(msg.id); };
          return (
            <g key={`beam-${s}-${t}-${i}`}
              role={clickable ? 'button' : undefined}
              tabIndex={clickable ? 0 : undefined}
              aria-label={tooltip}
              onClick={clickable ? selectMsg : undefined}
              onKeyDown={clickable ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectMsg(); } } : undefined}
              style={{ cursor: clickable ? 'pointer' : undefined, pointerEvents: 'auto' }}
            >
              <title>{tooltip}</title>
              {/* Wider, invisible hit-area so the thin dashed beam stays easy to hover/click.
                  The parent <svg> is pointer-events-none, so this needs its own 'auto'. */}
              <line
                x1={sPos.x} y1={sPos.y} x2={tPos.x} y2={tPos.y}
                stroke="transparent" strokeWidth={14} strokeLinecap="round"
                style={{ pointerEvents: 'auto' }}
              />
              <line
                x1={sPos.x} y1={sPos.y} x2={tPos.x} y2={tPos.y}
                stroke={color} strokeWidth={1.5} strokeOpacity={0.55}
                strokeDasharray="3 9" strokeLinecap="round"
                style={{ filter: `drop-shadow(0 0 4px ${color})` }}
              >
                {/* Flowing dashes = an active message/handoff between two agents. */}
                {!reduce && (
                  <animate
                    attributeName="stroke-dashoffset"
                    from="0" to="24" dur="1.1s" repeatCount="indefinite"
                  />
                )}
              </line>
            </g>
          );
        })}

        {/* Task-handoff beams — WS-7 item 2: a real task just completed and
            handed off to a teammate with in-flight work in the same mission. */}
        {resolvedHandoffs.map(({ h, from, to }) => (
          <TaskHandoffLine key={h.id} from={from} to={to} durationMs={HANDOFF_ANIMATION_MS} />
        ))}
      </svg>

      {/* Task-handoff labels — HTML overlay, travels alongside the beam above. */}
      {resolvedHandoffs.map(({ h, from, to }) => (
        <TaskHandoffLabel
          key={h.id} from={from} to={to} label={h.label}
          durationMs={HANDOFF_ANIMATION_MS} reduce={!!reduce}
        />
      ))}

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

      {/* Agent nodes — WS-7 item 1: a node mounts (and animates in + bursts)
          only the first time its id appears, i.e. only on a real
          org.agent.activated-family SSE event flowing into `agents`. */}
      {agents.map((agent, i) => {
        const pos = positions.get(agent.id);
        if (!pos) return null;
        return (
          <AgentSpawnNode
            key={agent.id}
            pos={pos}
            reduce={!!reduce}
            delay={Math.min(i, 8) * 0.04}
            onSpawn={p => canvasRef.current?.emitBurst(p, '#00D4FF', 10)}
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
          </AgentSpawnNode>
        );
      })}

      {/* Mission nodes */}
      {missions.filter(m => m.status === 'active').slice(0, 8).map(m => {
        const pos = positions.get(`mission-${m.id}`);
        if (!pos) return null;
        return (
          <motion.button
            key={m.id}
            className="absolute rounded-lg px-2 py-1 bg-[#0A0F1A] border border-[#6366F1]/30 text-[9px] text-[#6366F1] font-medium max-w-[80px] truncate hover:border-[#6366F1]/60 transition-colors"
            style={{ left: pos.x - 40, top: pos.y - 12, boxShadow: '0 0 8px rgba(99,102,241,0.20)' }}
            whileHover={reduce ? undefined : { scale: 1.08, y: -1 }}
            whileTap={reduce ? undefined : { scale: 0.96 }}
            transition={{ type: 'spring', stiffness: 600, damping: 35 }}
            onClick={() => onMissionSelect?.(m.id)}
            aria-label={`Mission: ${m.title}`}
          >
            {m.title.slice(0, 20)}
          </motion.button>
        );
      })}

      {/* Particle canvas */}
      <ParticleCanvas ref={canvasRef} width={CANVAS_W} height={CANVAS_H} />
    </div>
  );
}
