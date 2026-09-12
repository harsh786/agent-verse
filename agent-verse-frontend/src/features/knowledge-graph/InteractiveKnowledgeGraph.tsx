/**
 * InteractiveKnowledgeGraph — the glowing, force-directed view of the tenant's
 * knowledge graph. Fetches the whole graph (GET /knowledge-graph/export) and
 * renders it with the shared d3 `KnowledgeGraph` component. Reused by the
 * Graphify page (shown after a build) and the Knowledge Graph explorer (graph
 * view). Uses the shared ['kg-graph'] query key so a fresh Graphify build (which
 * invalidates that key) refreshes every mounted view.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Loader2, Network, RefreshCw } from 'lucide-react';

import { knowledgeGraphApi } from '@/lib/api/client';
import {
  KnowledgeGraph,
  type KnowledgeNode,
  type KnowledgeEdge,
} from '@/components/knowledge/KnowledgeGraph';

export function InteractiveKnowledgeGraph({
  height,
  fill = false,
  onNodeClick,
}: {
  /** Fixed pixel height. Omit and set `fill` to fill the parent instead. */
  height?: number;
  /** When true, the graph fills its parent (parent must have a height). */
  fill?: boolean;
  onNodeClick?: (node: KnowledgeNode) => void;
}) {
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['kg-graph'],
    queryFn: () => knowledgeGraphApi.getGraph(),
    staleTime: 15_000,
  });

  // Measure the container so the SVG fills the available space responsively.
  const wrapRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 800, height: height ?? 540 });
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const measure = () => {
      const w = Math.floor(el.clientWidth) || 800;
      const h = fill ? (Math.floor(el.clientHeight) || 540) : (height ?? 540);
      setSize({ width: w, height: h });
    };
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    measure();
    return () => ro.disconnect();
  }, [fill, height]);
  const { width } = size;

  const graphData = useMemo(
    () => ({
      nodes: (data?.nodes ?? []).map((n): KnowledgeNode => ({
        id: n.node_id,
        label: n.label,
        type: n.node_type,
      })),
      edges: (data?.edges ?? []).map((e): KnowledgeEdge => ({
        id: e.edge_id,
        source: e.source,
        target: e.target,
        label: e.edge_type,
      })),
    }),
    [data],
  );

  return (
    <div
      ref={wrapRef}
      className={`relative w-full rounded-2xl border border-white/10 bg-[#060810]/60 overflow-hidden
        ${fill ? 'h-full' : ''}`}
      style={fill ? undefined : { height: height ?? 540 }}
    >
      {/* Refresh */}
      <button
        onClick={() => refetch()}
        className="absolute top-3 right-3 z-10 p-2 rounded-lg bg-[#0F1117]/80 border border-white/10
                   text-[#F1F5F9]/50 hover:text-[#F1F5F9] transition-colors"
        aria-label="Refresh graph"
        title="Refresh graph"
      >
        <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? 'animate-spin' : ''}`} />
      </button>

      {isLoading ? (
        <div className="absolute inset-0 flex items-center justify-center text-[#F1F5F9]/40">
          <Loader2 className="h-6 w-6 animate-spin" />
        </div>
      ) : isError ? (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-red-400 text-sm">
          Failed to load the graph.
          <button onClick={() => refetch()} className="underline text-xs">Retry</button>
        </div>
      ) : graphData.nodes.length === 0 ? (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-center px-8">
          <Network className="h-8 w-8 text-[#F1F5F9]/20" />
          <p className="text-sm text-[#F1F5F9]/50">No graph yet.</p>
          <p className="text-xs text-[#F1F5F9]/30">
            Run Graphify on an organisation to materialise its nodes and edges.
          </p>
        </div>
      ) : (
        <KnowledgeGraph
          data={graphData}
          width={width}
          height={height}
          onNodeClick={onNodeClick}
        />
      )}
    </div>
  );
}

export default InteractiveKnowledgeGraph;
