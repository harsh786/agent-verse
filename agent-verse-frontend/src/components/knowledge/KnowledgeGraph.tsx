import { useRef, useEffect, useMemo, useState } from "react";
import type { JSX } from "react";
import type { SimulationNodeDatum, SimulationLinkDatum } from "d3-force";
import type { Selection } from "d3-selection";

/** Node type is an open string so callers can pass any backend node-type taxonomy
 *  (e.g. the tenant knowledge graph's 10 NodeType values) — unknown types fall
 *  back to a neutral color rather than being rejected. */
export interface KnowledgeNode extends SimulationNodeDatum {
  id: string;
  label: string;
  type: string;
}

export interface KnowledgeEdge extends SimulationLinkDatum<KnowledgeNode> {
  id: string;
  label?: string;
}

export interface KnowledgeGraphData {
  nodes: KnowledgeNode[];
  edges: KnowledgeEdge[];
}

interface KnowledgeGraphProps {
  data: KnowledgeGraphData;
  width?: number;
  height?: number;
  /** Extra color overrides/additions, merged over the built-in defaults. */
  colors?: Record<string, string>;
  /** Called (in addition to the built-in detail panel) when a node is clicked. */
  onNodeClick?: (node: KnowledgeNode) => void;
  /** Node id to focus — connected nodes/edges stay at full opacity, the rest dim.
   *  Pass `null`/`undefined` to clear focus. */
  focusNodeId?: string | null;
}

const DEFAULT_NODE_COLORS: Record<string, string> = {
  document: "#3b82f6",
  chunk: "#0ea5e9",
  concept: "#22c55e",
  entity: "#f59e0b",
  goal: "#a855f7",
  tool: "#ef4444",
  memory: "#14b8a6",
  artifact: "#eab308",
  agent: "#ec4899",
  workflow: "#6366f1",
};

const FALLBACK_COLOR = "#64748b";

export function KnowledgeGraph({
  data,
  width = 600,
  height = 400,
  colors,
  onNodeClick,
  focusNodeId = null,
}: KnowledgeGraphProps): JSX.Element {
  const svgRef = useRef<SVGSVGElement>(null);
  const [selected, setSelected] = useState<KnowledgeNode | null>(null);
  const nodeSelectionRef = useRef<Selection<SVGCircleElement, KnowledgeNode, SVGGElement, unknown> | null>(null);
  const linkSelectionRef = useRef<Selection<SVGLineElement, KnowledgeEdge, SVGGElement, unknown> | null>(null);
  const NODE_COLORS = useMemo(() => ({ ...DEFAULT_NODE_COLORS, ...colors }), [colors]);

  // Keep a live ref to onNodeClick so the simulation-building effect below doesn't
  // need it as a dependency — re-running it on every render would restart the
  // force layout whenever the parent passes a fresh callback identity.
  const onNodeClickRef = useRef(onNodeClick);
  useEffect(() => { onNodeClickRef.current = onNodeClick; }, [onNodeClick]);

  useEffect(() => {
    if (!data || !svgRef.current) return;
    const svg = svgRef.current;

    // Clear previous render
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    let rafId: number | null = null;

    (async () => {
      const { forceSimulation, forceLink, forceManyBody, forceCenter } = await import("d3-force");
      const { select } = await import("d3-selection");

      const nodes: KnowledgeNode[] = data.nodes.map((n) => ({ ...n }));
      const links: KnowledgeEdge[] = data.edges.map((e) => ({ ...e }));

      const d3svg = select<SVGSVGElement, unknown>(svg);

      const linkGroup = d3svg.append("g").attr("class", "links");
      const nodeGroup = d3svg.append("g").attr("class", "nodes");

      const linkEl = linkGroup
        .selectAll<SVGLineElement, KnowledgeEdge>("line")
        .data(links)
        .enter()
        .append("line")
        .attr("stroke", "hsl(214.3 31.8% 70%)")
        .attr("stroke-width", 1.5)
        .attr("stroke-opacity", 0.6);
      linkSelectionRef.current = linkEl;

      const nodeEl = nodeGroup
        .selectAll<SVGCircleElement, KnowledgeNode>("circle")
        .data(nodes)
        .enter()
        .append("circle")
        .attr("r", 8)
        .attr("fill", (d) => NODE_COLORS[d.type] ?? FALLBACK_COLOR)
        .attr("cursor", "pointer")
        .attr("stroke", "white")
        .attr("stroke-width", 1.5)
        .on("click", (_event, d) => {
          setSelected(d);
          onNodeClickRef.current?.(d);
        });
      nodeSelectionRef.current = nodeEl;

      const labelEl = nodeGroup
        .selectAll<SVGTextElement, KnowledgeNode>("text")
        .data(nodes)
        .enter()
        .append("text")
        .text((d) => d.label.slice(0, 18))
        .attr("font-size", "10px")
        .attr("fill", "hsl(215.4 16.3% 46.9%)")
        .attr("dx", 12)
        .attr("dy", 4)
        .attr("pointer-events", "none");

      const simulation = forceSimulation<KnowledgeNode>(nodes)
        .force(
          "link",
          forceLink<KnowledgeNode, KnowledgeEdge>(links)
            .id((d) => d.id)
            .distance(80)
        )
        .force("charge", forceManyBody<KnowledgeNode>().strength(-200))
        .force("center", forceCenter(width / 2, height / 2))
        .on("tick", () => {
          linkEl
            .attr("x1", (d) => (d.source as KnowledgeNode).x ?? 0)
            .attr("y1", (d) => (d.source as KnowledgeNode).y ?? 0)
            .attr("x2", (d) => (d.target as KnowledgeNode).x ?? 0)
            .attr("y2", (d) => (d.target as KnowledgeNode).y ?? 0);

          nodeEl.attr("cx", (d) => d.x ?? 0).attr("cy", (d) => d.y ?? 0);
          labelEl.attr("x", (d) => d.x ?? 0).attr("y", (d) => d.y ?? 0);
        });

      // Stop after settling
      rafId = requestAnimationFrame(() => {
        simulation.alphaDecay(0.05);
      });

      return () => simulation.stop();
    })();

    return () => {
      if (rafId !== null) cancelAnimationFrame(rafId);
    };
  }, [data, width, height, NODE_COLORS]);

  // Focus dimming: applied as a separate effect (not folded into the simulation
  // build above) so hovering/selecting a node doesn't restart the force layout —
  // it just fades non-connected nodes/edges via opacity.
  useEffect(() => {
    const nodeSel = nodeSelectionRef.current;
    const linkSel = linkSelectionRef.current;
    if (!nodeSel || !linkSel) return;

    if (!focusNodeId) {
      nodeSel.attr("opacity", 1);
      linkSel.attr("stroke-opacity", 0.6);
      return;
    }

    const connected = new Set<string>([focusNodeId]);
    for (const e of data.edges) {
      const s = typeof e.source === "object" ? (e.source as KnowledgeNode).id : e.source;
      const t = typeof e.target === "object" ? (e.target as KnowledgeNode).id : e.target;
      if (s === focusNodeId && t) connected.add(String(t));
      if (t === focusNodeId && s) connected.add(String(s));
    }

    nodeSel.attr("opacity", (d) => (connected.has(d.id) ? 1 : 0.15));
    linkSel.attr("stroke-opacity", (d) => {
      const s = typeof d.source === "object" ? (d.source as KnowledgeNode).id : d.source;
      const t = typeof d.target === "object" ? (d.target as KnowledgeNode).id : d.target;
      return s === focusNodeId || t === focusNodeId ? 0.9 : 0.08;
    });
  }, [focusNodeId, data]);

  return (
    <div className="relative">
      <svg
        ref={svgRef}
        width={width}
        height={height}
        className="w-full"
        viewBox={`0 0 ${width} ${height}`}
        aria-label="Knowledge graph visualization"
      />
      {/* Legend — only the types actually present in this graph */}
      <div className="flex flex-wrap gap-3 mt-2 text-xs text-muted-foreground">
        {[...new Set(data.nodes.map((n) => n.type))].sort().map((type) => (
          <span key={type} className="flex items-center gap-1">
            <span
              className="inline-block w-2.5 h-2.5 rounded-full"
              style={{ background: NODE_COLORS[type] ?? FALLBACK_COLOR }}
            />
            {type}
          </span>
        ))}
      </div>
      {/* Node detail panel */}
      {selected && (
        <div className="absolute bottom-8 right-0 bg-card border border-border rounded-lg px-3 py-2 text-xs shadow-md">
          <p className="font-semibold">{selected.label}</p>
          <p className="text-muted-foreground capitalize">{selected.type}</p>
          <button
            onClick={() => setSelected(null)}
            className="mt-1 text-primary hover:underline"
          >
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}
