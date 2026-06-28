/**
 * GoalDNAPage — interactive execution graph (Goal DNA).
 * Visualizes the entire goal run as a node-edge graph:
 * - Nodes: start, steps, tool calls, end
 * - Edges: data flow between nodes
 * Uses @xyflow/react (already installed) for clean rendering.
 */
import { useMemo, Component, type ReactNode, type ErrorInfo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ReactFlow, Background, Controls, BackgroundVariant, MarkerType, type Node, type Edge } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { insightsApi } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/Skeleton";
import { ArrowLeft, Zap, Wrench, GitBranch, CheckCircle2, XCircle, Info, AlertCircle } from "lucide-react";

const NODE_COLORS: Record<string, { bg: string; border: string; icon: React.ElementType }> = {
  start:  { bg: "bg-blue-50 dark:bg-blue-950/40",  border: "border-blue-300 dark:border-blue-700", icon: Zap },
  step:   { bg: "bg-violet-50 dark:bg-violet-950/40", border: "border-violet-300 dark:border-violet-700", icon: GitBranch },
  tool:   { bg: "bg-amber-50 dark:bg-amber-950/40", border: "border-amber-300 dark:border-amber-700", icon: Wrench },
  end:    { bg: "bg-green-50 dark:bg-green-950/40", border: "border-green-300 dark:border-green-700", icon: CheckCircle2 },
  failed: { bg: "bg-red-50 dark:bg-red-950/40",    border: "border-red-300 dark:border-red-700",   icon: XCircle },
};

function CustomNode({ data }: { data: Record<string, unknown> }) {
  const type = (data.nodeType as string) ?? "step";
  const config = NODE_COLORS[type === "end" && data.status === "goal_failed" ? "failed" : type] ?? NODE_COLORS.step;
  const Icon = config.icon;
  return (
    <div className={`border rounded-lg px-3 py-2 text-xs min-w-[100px] max-w-[160px] ${config.bg} ${config.border} shadow-sm`}>
      <div className="flex items-center gap-1.5 mb-1">
        <Icon className="h-3 w-3 shrink-0 opacity-70" aria-hidden="true" />
        <span className="font-medium truncate text-foreground">{data.label as string}</span>
      </div>
      {!!data.toolName && (
        <span className="block text-[10px] text-muted-foreground truncate">{String(data.toolName)}</span>
      )}
    </div>
  );
}

const NODE_TYPES = { custom: CustomNode };

class GraphErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean; error: string }> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { hasError: false, error: "" };
  }
  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error: error.message };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("GoalDNA graph error:", error, info);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="flex flex-col items-center justify-center h-64 text-muted-foreground border border-border rounded-xl">
          <AlertCircle className="h-8 w-8 mb-2 opacity-40" />
          <p className="text-sm">Failed to render execution graph</p>
          <p className="text-xs mt-1 opacity-60">{this.state.error}</p>
          <button
            onClick={() => this.setState({ hasError: false })}
            className="mt-3 text-xs text-primary hover:underline"
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

export function GoalDNAPage() {
  const { goalId } = useParams<{ goalId: string }>();
  const navigate = useNavigate();

  const { data: graph, isLoading, isError } = useQuery({
    queryKey: ["goal-dna", goalId],
    queryFn: () => insightsApi.getExecutionGraph(goalId!),
    enabled: !!goalId,
    staleTime: 300_000,
  });

  const { nodes, edges } = useMemo<{ nodes: Node[]; edges: Edge[] }>(() => {
    if (!graph) return { nodes: [], edges: [] };

    const xGap = 200;
    const yGap = 80;
    const flowNodes: Node[] = graph.nodes.map((n, i) => {
      const x = (i % 4) * xGap + (n.type === "tool" ? 50 : 0);
      const y = Math.floor(i / 4) * yGap;
      return {
        id: n.id,
        type: "custom",
        position: { x, y },
        data: { label: n.label, nodeType: n.type, ...n.data, toolName: n.data?.tool_name as string | undefined },
        draggable: true,
      };
    });

    const flowEdges: Edge[] = graph.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      markerEnd: { type: MarkerType.ArrowClosed, width: 12, height: 12 },
      style: { stroke: "hsl(var(--border))", strokeWidth: 1.5 },
      animated: false,
    }));

    return { nodes: flowNodes, edges: flowEdges };
  }, [graph]);

  return (
    <div className="flex flex-col h-full space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <button
          onClick={() => navigate(`/goals/${goalId}`)}
          className="p-1.5 rounded-lg hover:bg-muted/60 text-muted-foreground hover:text-foreground transition-colors"
          aria-label="Back to goal"
        >
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div>
          <h1 className="text-xl font-bold">Goal DNA</h1>
          <p className="text-sm text-muted-foreground">Execution graph for goal <code className="text-xs bg-muted px-1 rounded">{goalId?.slice(0, 12)}…</code></p>
        </div>
        {graph && (
          <div className="ml-auto flex gap-3 text-xs text-muted-foreground">
            <span><strong className="text-foreground">{graph.stats.total_nodes}</strong> nodes</span>
            <span><strong className="text-foreground">{graph.stats.tool_calls}</strong> tool calls</span>
            <span><strong className="text-foreground">{graph.stats.unique_tools}</strong> unique tools</span>
          </div>
        )}
      </div>

      {isLoading && (
        <div className="grid grid-cols-4 gap-3">
          {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-16 rounded-lg" />)}
        </div>
      )}

      {isError && (
        <div className="flex flex-col items-center justify-center h-64 text-muted-foreground">
          <Info className="h-8 w-8 mb-2 opacity-40" />
          <p className="text-sm">Could not load execution graph</p>
          <p className="text-xs mt-1 opacity-60">The goal may not have completed or events may not be available</p>
        </div>
      )}

      {graph && nodes.length === 0 && (
        <div className="flex flex-col items-center justify-center h-64 text-muted-foreground">
          <GitBranch className="h-8 w-8 mb-2 opacity-40" />
          <p className="text-sm">No execution events recorded</p>
        </div>
      )}

      {graph && nodes.length > 0 && (
        <GraphErrorBoundary>
          <div className="flex-1 border border-border rounded-xl overflow-hidden min-h-[500px]">
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={NODE_TYPES}
              fitView
              proOptions={{ hideAttribution: true }}
            >
              <Background variant={BackgroundVariant.Dots} gap={16} className="opacity-50" />
              <Controls />
            </ReactFlow>
          </div>
        </GraphErrorBoundary>
      )}
    </div>
  );
}
