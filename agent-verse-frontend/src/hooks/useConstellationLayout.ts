/**
 * useConstellationLayout — manages d3-force layout for the agent constellation.
 * Spec §3.3: Hybrid force-directed + hierarchical layout. 
 * Returns positions as a Map<agentId, {x,y}> updated each animation frame.
 */
import { useEffect, useRef, useCallback } from 'react';

export interface ConstellationNode {
  id:       string;
  type:     'agent' | 'mission' | 'tool' | 'knowledge';
  isFixed?: boolean;
  radius?:  number;
}

export interface ConstellationEdge {
  source: string;
  target: string;
  strength?: number;
}

export interface ConstellationLayoutOptions {
  width:    number;
  height:   number;
  onTick?:  (positions: Map<string, { x: number; y: number }>) => void;
}

export function useConstellationLayout(
  nodes: ConstellationNode[],
  edges: ConstellationEdge[],
  { width, height, onTick }: ConstellationLayoutOptions,
) {
  const simRef      = useRef<any>(null); // eslint-disable-line @typescript-eslint/no-explicit-any
  const animRef     = useRef<number | null>(null);
  const onTickRef   = useRef(onTick);
  onTickRef.current = onTick;

  const stopSim = useCallback(() => {
    if (animRef.current) cancelAnimationFrame(animRef.current);
    simRef.current?.stop();
  }, []);

  useEffect(() => {
    if (nodes.length === 0) return;

    let cancelled = false;
    (async () => {
      const d3 = await import('d3-force');
      if (cancelled) return;

      const cx = width  / 2;
      const cy = height / 2;

      // d3 mutates nodes in place, so we create copies
      const d3Nodes = nodes.map(n => ({
        id: n.id, type: n.type,
        x: cx + (Math.random() - 0.5) * 120,
        y: cy + (Math.random() - 0.5) * 120,
        fx: n.isFixed ? cx : undefined,
        fy: n.isFixed ? cy : undefined,
        r:  n.radius ?? (n.type === 'agent' ? 20 : 12),
      }));

      const d3Links = edges.map(e => ({
        source: e.source, target: e.target,
        strength: e.strength ?? 0.4,
      }));

      const sim = d3.forceSimulation(d3Nodes)
        .force('link',   d3.forceLink(d3Links).id((d: any) => (d as any).id).distance(120).strength((l: any) => l.strength ?? 0.4))
        .force('charge', d3.forceManyBody().strength(-300))
        .force('center', d3.forceCenter(cx, cy))
        .force('collide', d3.forceCollide().radius((d: any) => (d as any).r + 15))
        .force('x',      d3.forceX(cx).strength(0.03))
        .force('y',      d3.forceY(cy).strength(0.03))
        .alphaDecay(0.015);

      simRef.current = sim;

      function tick() {
        if (cancelled) return;
        const positions = new Map<string, { x: number; y: number }>();
        for (const n of d3Nodes) positions.set(n.id, { x: n.x ?? cx, y: n.y ?? cy });
        onTickRef.current?.(positions);
        animRef.current = requestAnimationFrame(tick);
      }
      tick();
    })();

    return () => {
      cancelled = true;
      stopSim();
    };
  }, [nodes.length, edges.length, width, height, stopSim]); // eslint-disable-line react-hooks/exhaustive-deps

  return { stopSim };
}
