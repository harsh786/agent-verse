/**
 * AgentOrbitView — animated force graph showing active agents + running goals.
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
}

const STATUS_COLORS: Record<string, string> = {
  active: "#22c55e",
  idle: "#6b7280",
  error: "#ef4444",
};

export function AgentOrbitView({
  agents,
  width = 320,
  height = 220,
  className = "",
}: AgentOrbitViewProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const animRef = useRef<number | null>(null);
  const simRef = useRef<any>(null); // eslint-disable-line @typescript-eslint/no-explicit-any

  useEffect(() => {
    if (!svgRef.current || agents.length === 0) return;

    // Lazy import d3 for tree-shaking
    let cancelled = false;
    (async () => {
      const d3force = await import("d3-force");
      if (cancelled) return;

      const cx = width / 2;
      const cy = height / 2;
      const svg = svgRef.current!;

      // Clear previous
      while (svg.firstChild) svg.removeChild(svg.firstChild);

      // Central core node
      const coreNode = { id: "__core__", x: cx, y: cy, fx: cx, fy: cy };
      const agentNodes = agents.map((a, i) => ({
        ...a,
        x: cx + Math.cos((i / agents.length) * 2 * Math.PI) * 80,
        y: cy + Math.sin((i / agents.length) * 2 * Math.PI) * 80,
        r: 12 + Math.min(a.goalCount, 3) * 3,
      }));
      const allNodes = [coreNode, ...agentNodes] as any[]; // eslint-disable-line @typescript-eslint/no-explicit-any
      const links = agentNodes.map((n) => ({ source: "__core__", target: n.id }));

      // D3 force simulation
      const sim = d3force
        .forceSimulation(allNodes)
        .force(
          "link",
          d3force
            .forceLink(links)
            .id((d: any) => (d as any).id) // eslint-disable-line @typescript-eslint/no-explicit-any
            .distance(80)
            .strength(0.5),
        )
        .force("charge", d3force.forceManyBody().strength(-60))
        .force("center", d3force.forceCenter(cx, cy))
        .force(
          "collision",
          d3force.forceCollide().radius((d: any) => ((d as any).r ?? 8) + 8), // eslint-disable-line @typescript-eslint/no-explicit-any
        )
        .alphaDecay(0.02);

      // Draw links
      const linkGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
      const linkEls = links.map(() => {
        const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
        line.setAttribute("stroke", "hsl(var(--border))");
        line.setAttribute("stroke-width", "1");
        line.setAttribute("stroke-opacity", "0.5");
        linkGroup.appendChild(line);
        return line;
      });
      svg.appendChild(linkGroup);

      // Draw core node
      const coreEl = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      coreEl.setAttribute("r", "14");
      coreEl.setAttribute("fill", "hsl(var(--primary))");
      coreEl.setAttribute("opacity", "0.9");
      coreEl.setAttribute("cx", String(cx));
      coreEl.setAttribute("cy", String(cy));
      svg.appendChild(coreEl);

      // Zap icon in center (text-based)
      const zap = document.createElementNS("http://www.w3.org/2000/svg", "text");
      zap.setAttribute("x", String(cx));
      zap.setAttribute("y", String(cy + 4));
      zap.setAttribute("text-anchor", "middle");
      zap.setAttribute("fill", "hsl(var(--primary-foreground))");
      zap.setAttribute("font-size", "12");
      zap.textContent = "⚡";
      svg.appendChild(zap);

      // Draw agent nodes
      const nodeEls = agentNodes.map((agent) => {
        const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
        g.style.cursor = "pointer";

        // Outer ring for active agents
        if (agent.status === "active") {
          const ring = document.createElementNS("http://www.w3.org/2000/svg", "circle");
          ring.setAttribute("r", String(agent.r + 4));
          ring.setAttribute("fill", "none");
          ring.setAttribute("stroke", STATUS_COLORS.active);
          ring.setAttribute("stroke-width", "1.5");
          ring.setAttribute("opacity", "0.4");
          g.appendChild(ring);
        }

        // Main circle
        const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        circle.setAttribute("r", String(agent.r));
        circle.setAttribute("fill", STATUS_COLORS[agent.status] ?? STATUS_COLORS.idle);
        circle.setAttribute("fill-opacity", "0.8");
        g.appendChild(circle);

        // Label
        const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
        label.setAttribute("y", String(agent.r + 12));
        label.setAttribute("text-anchor", "middle");
        label.setAttribute("font-size", "9");
        label.setAttribute("fill", "hsl(var(--muted-foreground))");
        label.textContent = agent.label.slice(0, 12);
        g.appendChild(label);

        svg.appendChild(g);
        return { g, agent };
      });

      // Animate
      function animate() {
        // Update link positions — use the local `links` array (d3 mutates source/target in-place)
        (links as any[]).forEach((link: any, i: number) => { // eslint-disable-line @typescript-eslint/no-explicit-any
          const el = linkEls[i];
          if (el && link.source && link.target) {
            el.setAttribute("x1", String(link.source.x ?? cx));
            el.setAttribute("y1", String(link.source.y ?? cy));
            el.setAttribute("x2", String(link.target.x ?? cx));
            el.setAttribute("y2", String(link.target.y ?? cy));
          }
        });

        // Update node positions
        nodeEls.forEach(({ g }, i) => {
          const n = allNodes[i + 1];
          if (n) {
            g.setAttribute("transform", `translate(${n.x ?? cx}, ${n.y ?? cy})`);
          }
        });

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
  }, [agents, width, height]);

  if (agents.length === 0) {
    return (
      <div
        className={`flex items-center justify-center text-muted-foreground text-sm ${className}`}
        style={{ height }}
      >
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
