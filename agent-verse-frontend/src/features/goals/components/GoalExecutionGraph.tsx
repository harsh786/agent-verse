/**
 * GoalExecutionGraph — React Flow DAG that renders goal execution live.
 * Spec §4: Node types with distinct visuals, particle overlay, live state.
 */
import { useRef } from 'react';
import { ReactFlow, Background, Controls, type NodeProps, type EdgeProps, getBezierPath } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { motion, useReducedMotion } from 'framer-motion';
import { CheckCircle2, XCircle, Loader2, Clock, Shield, Zap, BookOpen, RefreshCw } from 'lucide-react';
import { useGoalExecutionGraph, type GraphNodeType, type GraphNodeStatus } from '@/hooks/useGoalExecutionGraph';
import { ParticleCanvas, type ParticleCanvasRef } from '@/components/canvas/ParticleCanvas';
import { useSSEToParticles } from '@/hooks/useSSEToParticles';
import type { GoalEvent } from '@/lib/sse/useGoalStream';
import { cn } from '@/lib/utils';

// ── Node color/icon config ────────────────────────────────────────────────────
const NODE_CONFIG: Record<GraphNodeType, { glow: string; icon: React.ElementType; bg: string }> = {
  start:      { glow: '#00D4FF', icon: Zap,          bg: '#0A0F1A' },
  plan:       { glow: '#6366F1', icon: BookOpen,      bg: '#0A0F1A' },
  step:       { glow: '#00D4FF', icon: Loader2,       bg: '#0A0F1A' },
  tool:       { glow: '#FFB300', icon: Zap,           bg: '#0A0F1A' },
  verify:     { glow: '#00E676', icon: CheckCircle2,  bg: '#0A0F1A' },
  hitl:       { glow: '#FFB300', icon: Clock,         bg: '#0A0F1A' },
  guardrail:  { glow: '#FF3366', icon: Shield,        bg: '#0A0F1A' },
  knowledge:  { glow: '#34D399', icon: BookOpen,      bg: '#0A0F1A' },
  replan:     { glow: '#FFB300', icon: RefreshCw,     bg: '#0A0F1A' },
  complete:   { glow: '#00E676', icon: CheckCircle2,  bg: '#0A0F1A' },
  failed:     { glow: '#FF3366', icon: XCircle,       bg: '#0A0F1A' },
};

const STATUS_GLOW: Record<GraphNodeStatus, string> = {
  pending: '0 0 4px rgba(255,255,255,0.08)',
  active:  '0 0 16px rgba(0,212,255,0.40), 0 0 4px rgba(0,212,255,0.70)',
  done:    '0 0 12px rgba(0,230,118,0.30)',
  failed:  '0 0 12px rgba(255,51,102,0.40)',
  blocked: '0 0 12px rgba(255,51,102,0.40)',
  waiting: '0 0 12px rgba(255,179,0,0.40)',
};

// ── Execution Node ────────────────────────────────────────────────────────────
function ExecNode({ data }: NodeProps) {
  const d       = data as { label: string; status: GraphNodeStatus; nodeType: GraphNodeType; output?: string };
  const cfg     = NODE_CONFIG[d.nodeType] ?? NODE_CONFIG.step;
  const Icon    = cfg.icon;
  const reduce  = useReducedMotion();

  return (
    <motion.div
      initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.6, filter: 'blur(4px)' }}
      animate={{ opacity: 1, scale: 1, filter: 'blur(0px)' }}
      transition={{ type: 'spring', stiffness: 380, damping: 30 }}
      style={{ boxShadow: STATUS_GLOW[d.status] }}
      className="rounded-xl bg-[#0A0F1A] border border-white/10 px-3 py-2 min-w-[140px] max-w-[200px]"
    >
      <div className="flex items-center gap-2 mb-1">
        <Icon className={cn(
          'h-3.5 w-3.5 flex-shrink-0',
          d.status === 'active'  && 'animate-spin text-[#00D4FF]',
          d.status === 'done'    && 'text-[#00E676]',
          d.status === 'failed'  && 'text-[#FF3366]',
          d.status === 'waiting' && 'text-[#FFB300]',
          d.status === 'blocked' && 'text-[#FF3366]',
          d.status === 'pending' && 'text-[#475569]',
        )} aria-hidden />
        <span className="text-[10px] font-medium text-[#F0F6FF] leading-tight truncate" style={{ maxWidth: 140 }}>
          {d.label}
        </span>
      </div>
      <span className="text-[9px] font-mono text-[#5A7494] capitalize">{d.status}</span>
      {d.output && (
        <p className="text-[9px] text-[#A0B4CC] mt-1 line-clamp-2 font-mono">{String(d.output).slice(0, 80)}</p>
      )}
      {/* Active pulse ring */}
      {d.status === 'active' && !reduce && (
        <motion.div
          className="absolute inset-0 rounded-xl border"
          style={{ borderColor: cfg.glow }}
          animate={{ opacity: [0.6, 0.1, 0.6] }}
          transition={{ duration: 1.8, repeat: Infinity, ease: 'easeInOut' }}
        />
      )}
    </motion.div>
  );
}

// ── Animated edge ─────────────────────────────────────────────────────────────
function AnimatedEdge({ id, sourceX, sourceY, targetX, targetY, data }: EdgeProps) {
  const [path] = getBezierPath({ sourceX, sourceY, targetX, targetY });
  const color = (data as { color?: string })?.color ?? '#1E2C4A';
  return (
    <g>
      <path id={id} d={path} stroke={color} strokeWidth={1.5} fill="none" strokeOpacity={0.4} />
      <circle r="4" fill={color} style={{ filter: `drop-shadow(0 0 4px ${color})` }}>
        <animateMotion dur="1.2s" repeatCount="indefinite" path={path} />
      </circle>
    </g>
  );
}

const NODE_TYPES = { exec: ExecNode };
const EDGE_TYPES = { animated: AnimatedEdge };

// ── Main component ────────────────────────────────────────────────────────────
interface GoalExecutionGraphProps {
  events:    GoalEvent[];
  className?: string;
}

export function GoalExecutionGraph({ events, className }: GoalExecutionGraphProps) {
  const graph      = useGoalExecutionGraph(events);
  const canvasRef  = useRef<ParticleCanvasRef>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Build positions map for particles
  const posMap = new Map<string, { x: number; y: number }>();
  for (const node of graph.nodes) {
    posMap.set(node.id, node.position);
    if (node.data.step) posMap.set(String(node.data.step), node.position);
    if (node.data.tool)  posMap.set(String(node.data.tool),  node.position);
  }
  useSSEToParticles(events, posMap, canvasRef);

  // Convert to React Flow format
  const rfNodes = graph.nodes.map(n => ({
    id:       n.id,
    type:     'exec',
    position: n.position,
    data:     { label: n.label, status: n.status, nodeType: n.type, ...n.data },
  }));

  const rfEdges = graph.edges.map(e => ({
    id:     e.id,
    source: e.source,
    target: e.target,
    type:   'animated',
    data:   { color: e.color },
    animated: e.animated,
  }));

  // Re-fit the viewport whenever the node count changes. The `fitView` prop only
  // fits on mount (when 0–1 nodes exist); for a completed goal all nodes stream
  // in afterwards, so without this the view stays panned off-screen and the
  // graph looks blank. A short delay lets the new nodes measure/layout first.

  return (
    <div ref={containerRef} className={cn('relative bg-[#020408] rounded-xl overflow-hidden', className)} style={{ height: 480 }}>
      {/* Token stats HUD */}
      <div className="absolute top-2 right-2 z-10 flex gap-2 text-[10px] font-mono">
        {graph.tokenStats.output > 0 && (
          <span className="px-2 py-1 rounded bg-[#0A0F1A]/80 text-[#00D4FF] border border-[#00D4FF]/20">
            {graph.tokenStats.output} tok
          </span>
        )}
        {graph.guardrailStats.fired > 0 && (
          <span className="px-2 py-1 rounded bg-[#FF3366]/10 text-[#FF3366] border border-[#FF3366]/20">
            🛡 {graph.guardrailStats.fired} blocked
          </span>
        )}
        {graph.hitlStats.pending > 0 && (
          <span className="px-2 py-1 rounded bg-[#FFB300]/10 text-[#FFB300] border border-[#FFB300]/20 animate-pulse">
            ⏳ {graph.hitlStats.pending} pending
          </span>
        )}
      </div>

      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        // Anchor the view to the top-left of the flow at a readable zoom. fitView
        // is unreliable here (nodes mount during the page-enter animation, so
        // their sizes aren't measured when it runs, and this tall vertical DAG
        // wouldn't fit legibly anyway); a fixed default viewport always shows the
        // Goal → Plan → Steps flow and stays pannable/zoomable.
        defaultViewport={{ x: 60, y: 24, zoom: 0.7 }}
        minZoom={0.2}
        proOptions={{ hideAttribution: true }}
        style={{ background: 'transparent' }}
      >
        <Background color="#1E2C4A" gap={24} size={1} />
        <Controls className="!bg-[#0A0F1A] !border-white/10 !shadow-none" />
      </ReactFlow>

      {/* Particle overlay — positioned absolute */}
      <ParticleCanvas ref={canvasRef} width={800} height={600} />
    </div>
  );
}
