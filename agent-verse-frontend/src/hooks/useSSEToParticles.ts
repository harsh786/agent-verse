/**
 * useSSEToParticles — bridges useGoalStream SSE events to ParticleCanvas emissions.
 * Spec §2.4: Every SSE event maps to a particle type with exact color/shape/speed.
 */
import { useEffect, useRef } from 'react';
import type { ParticleCanvasRef } from '@/components/canvas/ParticleCanvas';
import type { GoalEvent } from '@/lib/sse/useGoalStream';

/** Pixel positions of named nodes in the execution graph (set by GoalExecutionGraph) */
export type NodePositionMap = Map<string, { x: number; y: number }>;

const EVENT_PARTICLES: Record<string, {
  color: string; size: number; shape: 'circle' | 'spark' | 'diamond' | 'star' | 'x';
  trail: boolean; trailLength: number; duration: number;
}> = {
  token_chunk:                 { color: 'rgba(0,212,255,0.7)', size: 2, shape: 'circle', trail: true,  trailLength: 8,  duration: 300 },
  tool_call_complete:          { color: '#00E676', size: 4, shape: 'spark',   trail: true,  trailLength: 12, duration: 500 },
  tool_call_failed:            { color: '#FF3366', size: 4, shape: 'x',       trail: false, trailLength: 0,  duration: 400 },
  tool_call_pending_approval:  { color: '#FFB300', size: 4, shape: 'diamond', trail: true,  trailLength: 0,  duration: 800 },
  step_started:                { color: '#6366F1', size: 3, shape: 'circle',  trail: true,  trailLength: 6,  duration: 600 },
  step_complete:               { color: '#00E676', size: 3, shape: 'circle',  trail: true,  trailLength: 8,  duration: 500 },
  knowledge_retrieved:         { color: '#34D399', size: 3, shape: 'diamond', trail: true,  trailLength: 0,  duration: 700 },
  guardrail_rejected:          { color: '#FF3366', size: 5, shape: 'x',       trail: false, trailLength: 0,  duration: 300 },
  tool_call_denied:            { color: '#FF3366', size: 5, shape: 'x',       trail: false, trailLength: 0,  duration: 300 },
  hitl_approved:               { color: '#00E676', size: 5, shape: 'star',    trail: true,  trailLength: 0,  duration: 900 },
  child_agent_spawned:         { color: '#A855F7', size: 4, shape: 'star',    trail: true,  trailLength: 0,  duration: 1200 },
  plan_ready:                  { color: '#6366F1', size: 3, shape: 'circle',  trail: true,  trailLength: 6,  duration: 800 },
  verification_done:           { color: '#00E676', size: 3, shape: 'circle',  trail: true,  trailLength: 6,  duration: 600 },
  cache_hit:                   { color: 'rgba(0,212,255,0.4)', size: 2, shape: 'circle', trail: false, trailLength: 0, duration: 300 },
  artifact_captured:           { color: '#FFB300', size: 3, shape: 'diamond', trail: true,  trailLength: 0,  duration: 700 },
  pii_redacted:                { color: '#FFB300', size: 3, shape: 'diamond', trail: false, trailLength: 0,  duration: 400 },
  replan:                      { color: '#FFB300', size: 4, shape: 'spark',   trail: true,  trailLength: 6,  duration: 600 },
  debate_claim:                { color: '#A855F7', size: 2, shape: 'circle',  trail: true,  trailLength: 0,  duration: 700 },
  grounding_warning:           { color: '#FFB300', size: 3, shape: 'spark',   trail: false, trailLength: 0,  duration: 500 },
};

function getId() { return Math.random().toString(36).slice(2); }

export function useSSEToParticles(
  events:        GoalEvent[],
  nodePositions: NodePositionMap,
  canvasRef:     React.RefObject<ParticleCanvasRef | null>,
) {
  const lastLen = useRef(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const newEvents = events.slice(lastLen.current);
    lastLen.current = events.length;

    for (const evt of newEvents) {
      const cfg = EVENT_PARTICLES[evt.type];
      if (!cfg) continue;

      const stepId   = typeof evt.step === 'string' ? evt.step : 'step';
      const toolId   = typeof evt.tool_name === 'string' ? evt.tool_name : (typeof evt.tool === 'string' ? evt.tool : 'tool');
      const fromPos  = nodePositions.get('llm') ?? nodePositions.get(stepId) ?? nodePositions.get('plan');
      const toPos    = nodePositions.get(stepId)  ?? nodePositions.get('output') ?? nodePositions.get('verify');

      if (!fromPos || !toPos) {
        // No known positions — emit burst at center
        canvas.emitBurst({ x: 300, y: 200 }, cfg.color, 6);
        continue;
      }

      if (evt.type === 'tool_call_complete' || evt.type === 'tool_call_failed') {
        const toolPos = nodePositions.get(toolId) ?? toPos;
        canvas.emitBurst(toolPos, cfg.color, evt.type === 'tool_call_failed' ? 4 : 8);
      } else if (evt.type === 'guardrail_rejected' || evt.type === 'tool_call_denied') {
        const pos = nodePositions.get('guardrail') ?? toPos;
        canvas.emitBurst(pos, cfg.color, 12);
      } else if (evt.type === 'child_agent_spawned') {
        // Arc from parent to child
        canvas.emitParticle({
          id: getId(), from: fromPos, to: { x: toPos.x + 80, y: toPos.y - 60 },
          color: cfg.color, size: cfg.size, trail: cfg.trail,
          trailLength: cfg.trailLength, duration: cfg.duration, shape: cfg.shape,
        });
      } else {
        canvas.emitParticle({
          id: getId(), from: fromPos, to: toPos,
          color: cfg.color, size: cfg.size, trail: cfg.trail,
          trailLength: cfg.trailLength, duration: cfg.duration, shape: cfg.shape,
        });
      }
    }
  }, [events, nodePositions, canvasRef]);
}
