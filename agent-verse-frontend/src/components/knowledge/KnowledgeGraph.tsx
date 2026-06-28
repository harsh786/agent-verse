import { useRef, useEffect, useState } from "react";
import type { JSX } from "react";
import type { SimulationNodeDatum, SimulationLinkDatum } from "d3-force";

export interface KnowledgeNode extends SimulationNodeDatum {
  id: string;
  label: string;
  type: "document" | "concept" | "entity";
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
}

const NODE_COLORS: Record<KnowledgeNode["type"], string> = {
  document: "#3b82f6",
  concept: "#22c55e",
  entity: "#f59e0b",
};

export function KnowledgeGraph({
  data,
  width = 600,
  height = 400,
}: KnowledgeGraphProps): JSX.Element {
  const svgRef = useRef<SVGSVGElement>(null);
  const [selected, setSelected] = useState<KnowledgeNode | null>(null);

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

      const nodeEl = nodeGroup
        .selectAll<SVGCircleElement, KnowledgeNode>("circle")
        .data(nodes)
        .enter()
        .append("circle")
        .attr("r", 8)
        .attr("fill", (d) => NODE_COLORS[d.type] ?? "#64748b")
        .attr("cursor", "pointer")
        .attr("stroke", "white")
        .attr("stroke-width", 1.5)
        .on("click", (_event, d) => {
          setSelected(d);
        });

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
  }, [data, width, height]);

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
      {/* Legend */}
      <div className="flex gap-4 mt-2 text-xs text-muted-foreground">
        {(Object.entries(NODE_COLORS) as [KnowledgeNode["type"], string][]).map(([type, color]) => (
          <span key={type} className="flex items-center gap-1">
            <span
              className="inline-block w-2.5 h-2.5 rounded-full"
              style={{ background: color }}
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
