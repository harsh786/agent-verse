/**
 * AgentOrbitView — animated force graph showing active agents + running goals.
 * Spec §5.2 upgrade: orbit trails, goal-count rings, breathing, message beams.
 * Uses d3-force for physics simulation (all open source, no cloud deps).
 */
import { useEffect, useRef } from "react";

interface AgentNode {
  id: string;
  label: string;
  status: "active" | "idle" | "error";
  goalCount: number;
}

interface AgentOrbitViewProps {
  agents: AgentNode[];
  width?: number;
  height?: number;
  className?: string;
  /** Optional: pairs of agent IDs that are communicating (to show beams) */
  communicatingPairs?: [string, string][];
}

const STATUS_COLORS: Record<string, string> = {
  active: "#00D4FF",
  idle:   "#475569",
  error:  "#FF3366",
};

export function AgentOrbitView({
  agents, width = 320, height = 220, className = "", communicatingPairs = [],
}: AgentOrbitViewProps) {
  const svgRef  = useRef<SVGSVGElement>(null);
  const animRef = useRef<number | null>(null);
  const simRef  = useRef<any>(null); // eslint-disable-line @typescript-eslint/no-explicit-any
  // Trail history per node: nodeId → [{x,y}]
  const trailsRef = useRef<Record<string, Array<{x:number;y:number}>>>({});
  const frameRef  = useRef(0);

  useEffect(() => {
    if (!svgRef.current || agents.length === 0) return;

    let cancelled = false;
    (async () => {
      const d3force = await import("d3-force");
      if (cancelled) return;

      const cx = width / 2;
      const cy = height / 2;
      const svg = svgRef.current!;
      while (svg.firstChild) svg.removeChild(svg.firstChild);

      // SVG defs: glow filter
      const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
      defs.innerHTML = `
        <filter id="glow-active" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="3" result="coloredBlur"/>
          <feMerge><feMergeNode in="coloredBlur"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
        <filter id="glow-dim" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="1.5" result="coloredBlur"/>
          <feMerge><feMergeNode in="coloredBlur"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
      `;
      svg.appendChild(defs);

      // Trail group (rendered below everything)
      const trailGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      svg.appendChild(trailGroup);

      // Beam group (for communicating pairs)
      const beamGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      svg.appendChild(beamGroup);

      const coreNode = { id: "__core__", x: cx, y: cy, fx: cx, fy: cy };
      const agentNodes = agents.map((a, i) => ({
        ...a,
        x: cx + Math.cos((i / agents.length) * 2 * Math.PI) * 80,
        y: cy + Math.sin((i / agents.length) * 2 * Math.PI) * 80,
        r: 10 + Math.min(a.goalCount, 4) * 2,
      }));
      const allNodes = [coreNode, ...agentNodes] as any[]; // eslint-disable-line @typescript-eslint/no-explicit-any
      const links = agentNodes.map((n) => ({ source: "__core__", target: n.id }));

      const sim = d3force.forceSimulation(allNodes)
        .force("link", d3force.forceLink(links).id((d: any) => (d as any).id).distance(80).strength(0.5))
        .force("charge", d3force.forceManyBody().strength(-70))
        .force("center", d3force.forceCenter(cx, cy))
        .force("collision", d3force.forceCollide().radius((d: any) => ((d as any).r ?? 8) + 10))
        .alphaDecay(0.02);

      // Link lines
      const linkGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
      const linkEls = links.map(() => {
        const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
        line.setAttribute("stroke", "rgba(255,255,255,0.07)");
        line.setAttribute("stroke-width", "1");
        linkGroup.appendChild(line);
        return line;
      });
      svg.appendChild(linkGroup);

      // Core node
      const coreEl = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      coreEl.setAttribute("r", "14");
      coreEl.setAttribute('fill', '#00D4FF');
      coreEl.setAttribute('opacity', '0.9');
      coreEl.setAttribute('filter', 'url(#glow-active)');
      coreEl.setAttribute("cx", String(cx));
      coreEl.setAttribute("cy", String(cy));
      svg.appendChild(coreEl);
      const zapText = document.createElementNS("http://www.w3.org/2000/svg", "text");
      zapText.setAttribute("x", String(cx));
      zapText.setAttribute("y", String(cy + 4));
      zapText.setAttribute("text-anchor", "middle");
      zapText.setAttribute("fill", "#020408");
      zapText.setAttribute("font-size", "12");
      zapText.textContent = "⚡";
      svg.appendChild(zapText);

      // Agent nodes
      const nodeEls = agentNodes.map((agent) => {
        const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
        g.style.cursor = "pointer";

        // Goal-count concentric rings (spec §5.2)
        const ringCount = Math.min(agent.goalCount, 3);
        for (let ri = 0; ri < ringCount; ri++) {
          const ring = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
          ring.setAttribute('r', String(agent.r + 5 + ri * 5));
          ring.setAttribute('fill', 'none');
          ring.setAttribute('stroke', STATUS_COLORS[agent.status] ?? STATUS_COLORS.idle);
          ring.setAttribute('stroke-width', '0.8');
          ring.setAttribute('opacity', String(0.3 - ri * 0.08));
          g.appendChild(ring);
        }

        // Active outer ring
        if (agent.status === 'active') {
          const outerRing = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
          outerRing.setAttribute('r', String(agent.r + 6));
          outerRing.setAttribute('fill', 'none');
          outerRing.setAttribute('stroke', STATUS_COLORS.active);
          outerRing.setAttribute('stroke-width', '1.5');
          outerRing.setAttribute('opacity', '0.5');
          g.appendChild(outerRing);
        }

        const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        circle.setAttribute('r', String(agent.r));
        circle.setAttribute('fill', STATUS_COLORS[agent.status] ?? STATUS_COLORS.idle);
        circle.setAttribute('fill-opacity', '0.85');
        if (agent.status === 'active') circle.setAttribute('filter', 'url(#glow-active)');
        g.appendChild(circle);

        const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
        label.setAttribute("y", String(agent.r + 12));
        label.setAttribute("text-anchor", "middle");
        label.setAttribute("font-size", "9");
        label.setAttribute("fill", "#A0B4CC");
        label.textContent = agent.label.slice(0, 12);
        g.appendChild(label);

        svg.appendChild(g);
        return { g, agent };
      });

      // Initialize trails
      agentNodes.forEach(a => { trailsRef.current[a.id] = []; });

      function animate() {
        frameRef.current++;

        // Update links
        (links as any[]).forEach((link: any, i: number) => { // eslint-disable-line @typescript-eslint/no-explicit-any
          const el = linkEls[i];
          if (el && link.source && link.target) {
            el.setAttribute("x1", String(link.source.x ?? cx));
            el.setAttribute("y1", String(link.source.y ?? cy));
            el.setAttribute("x2", String(link.target.x ?? cx));
            el.setAttribute("y2", String(link.target.y ?? cy));
          }
        });

        // Update node positions + breathing
        nodeEls.forEach(({ g, agent }, i) => {
          const n = allNodes[i + 1];
          if (!n) return;
          const nx = n.x ?? cx;
          const ny = n.y ?? cy;

          // Breathing scale for active agents (spec §5.2)
          let scale = 1;
          if (agent.status === 'active') {
            scale = 0.97 + 0.06 * Math.sin(frameRef.current * 0.03 + i * 1.2);
          }
          g.setAttribute("transform", `translate(${nx}, ${ny}) scale(${scale})`);

          // Orbit trails — record position every 3 frames for active agents
          if (agent.status === 'active' && frameRef.current % 3 === 0) {
            const trail = trailsRef.current[agent.id] ?? [];
            trail.push({ x: nx, y: ny });
            if (trail.length > 20) trail.shift();
            trailsRef.current[agent.id] = trail;
          }
        });

        // Render trails
        while (trailGroup.firstChild) trailGroup.removeChild(trailGroup.firstChild);
        for (const [, trail] of Object.entries(trailsRef.current)) {
          for (let ti = 1; ti < trail.length; ti++) {
            const seg = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            seg.setAttribute('x1', String(trail[ti-1].x));
            seg.setAttribute('y1', String(trail[ti-1].y));
            seg.setAttribute('x2', String(trail[ti].x));
            seg.setAttribute('y2', String(trail[ti].y));
            seg.setAttribute('stroke', '#00D4FF');
            seg.setAttribute('stroke-width', '1.5');
            seg.setAttribute('stroke-opacity', String(0.05 + 0.12 * (ti / trail.length)));
            trailGroup.appendChild(seg);
          }
        }

        // Render communication beams (spec §5.2)
        while (beamGroup.firstChild) beamGroup.removeChild(beamGroup.firstChild);
        for (const [srcId, tgtId] of communicatingPairs) {
          const srcNode = allNodes.find((n: any) => n.id === srcId);
          const tgtNode = allNodes.find((n: any) => n.id === tgtId);
          if (!srcNode || !tgtNode) continue;
          const beamLine = document.createElementNS('http://www.w3.org/2000/svg', 'line');
          beamLine.setAttribute('x1', String(srcNode.x ?? cx));
          beamLine.setAttribute('y1', String(srcNode.y ?? cy));
          beamLine.setAttribute('x2', String(tgtNode.x ?? cx));
          beamLine.setAttribute('y2', String(tgtNode.y ?? cy));
          beamLine.setAttribute('stroke', '#A855F7');
          beamLine.setAttribute('stroke-width', '1.5');
          beamLine.setAttribute('stroke-opacity', '0.6');
          beamLine.setAttribute('filter', 'url(#glow-dim)');
          beamGroup.appendChild(beamLine);
        }

        animRef.current = requestAnimationFrame(animate);
      }
      animate();
      simRef.current = sim;
    })();

    return () => {
      cancelled = true;
      if (animRef.current) cancelAnimationFrame(animRef.current);
      simRef.current?.stop();
    };
  }, [agents, width, height, communicatingPairs]);

  if (agents.length === 0) {
    return (
      <div className={`flex items-center justify-center text-[#5A7494] text-sm ${className}`} style={{ height }}>
        No active agents
      </div>
    );
  }

  return (
    <svg
      ref={svgRef}
      width={width}
      height={height}
      className={className}
      aria-label="Agent activity visualization"
      role="img"
      viewBox={`0 0 ${width} ${height}`}
    />
  );
}
