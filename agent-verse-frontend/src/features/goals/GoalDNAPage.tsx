/**
 * GoalDNAPage — World-Class Execution Graph Visualizer
 *
 * Features:
 *   • Full ReactFlow graph with MiniMap, zoom/pan, fit-view
 *   • Custom rich node types: Start, Step, Tool (success/failed), End
 *   • Node inspector side-panel with full details on click
 *   • Animated edges for active/recent tool calls
 *   • Legend, stats bar, toolbar with keyboard shortcuts
 *   • Timeline sidebar showing execution sequence
 *   • Export as PNG
 *   • Error boundary around the graph
 *   • Responsive layout
 */

import {
  Component,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ErrorInfo,
  type ReactNode,
} from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  MarkerType,
  MiniMap,
  type Node,
  type Edge,
  type NodeProps,
  Panel,
  ReactFlowProvider,
  useReactFlow,
  getNodesBounds,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Clock,
  Code2,
  Download,
  GitBranch,
  Info,
  Layers,
  Maximize2,
  Minus,
  Plus,
  RefreshCw,
  Target,
  Wrench,
  X,
  XCircle,
  Zap,
} from 'lucide-react';
import { insightsApi, goalsApi } from '@/lib/api/client';
import { Skeleton } from '@/components/ui/Skeleton';
import { toast } from '@/stores/toast';
import { layeredLayout, type FlowNodeInput, type FlowEdgeInput } from '@/components/graph/FlowCanvas';

// ── Types ──────────────────────────────────────────────────────────────────────

interface DnaNodeData extends Record<string, unknown> {
  label: string;
  nodeType: 'start' | 'step' | 'tool' | 'end' | 'failed';
  status?: string;
  description?: string;
  toolName?: string;
  serverId?: string;
  outputPreview?: string;
  durationMs?: number;
  error?: string;
}

// ── Node styling ──────────────────────────────────────────────────────────────

const NODE_STYLES: Record<string, {
  bg: string; border: string; text: string; iconColor: string; icon: React.ComponentType<{ className?: string }>;
}> = {
  start: { bg: 'bg-blue-50', border: 'border-blue-300', text: 'text-blue-800', iconColor: 'text-blue-500', icon: Zap },
  step:  { bg: 'bg-violet-50', border: 'border-violet-300', text: 'text-violet-800', iconColor: 'text-violet-500', icon: Layers },
  tool:  { bg: 'bg-amber-50', border: 'border-amber-300', text: 'text-amber-800', iconColor: 'text-amber-500', icon: Wrench },
  end:   { bg: 'bg-green-50', border: 'border-green-300', text: 'text-green-800', iconColor: 'text-green-500', icon: CheckCircle2 },
  failed:{ bg: 'bg-red-50', border: 'border-red-300', text: 'text-red-800', iconColor: 'text-red-500', icon: XCircle },
};

// ── Custom Node Component ─────────────────────────────────────────────────────

function DnaNode({ data, selected }: NodeProps) {
  const d = data as unknown as DnaNodeData;
  // Override tool node style if it failed
  const typeKey = (d.nodeType === 'tool' && d.status === 'failed') ? 'failed' : (d.nodeType || 'step');
  const style = NODE_STYLES[typeKey] ?? NODE_STYLES.step;
  const Icon = style.icon;

  return (
    <div
      className={`
        rounded-xl border-2 px-3 py-2.5 min-w-[140px] max-w-[200px]
        shadow-sm transition-[color,background-color,border-color,opacity,box-shadow,transform] duration-150
        ${style.bg} ${style.border}
        ${selected ? 'ring-2 ring-primary ring-offset-1 shadow-md scale-105' : 'hover:shadow-md hover:scale-[1.02]'}
      `}
    >
      <div className="flex items-center gap-2">
        <Icon className={`h-3.5 w-3.5 shrink-0 ${style.iconColor}`} />
        <span className={`text-xs font-semibold truncate ${style.text}`}>
          {d.label}
        </span>
      </div>
      {d.toolName && d.nodeType === 'tool' && (
        <div className="mt-1 text-[10px] text-muted-foreground font-mono truncate pl-5">
          {d.toolName}
        </div>
      )}
      {d.status === 'failed' && d.nodeType === 'tool' && (
        <div className="mt-1 pl-5">
          <span className="text-[10px] bg-red-100 text-red-600 px-1 rounded">failed</span>
        </div>
      )}
      {d.durationMs && (
        <div className="mt-1 text-[10px] text-muted-foreground pl-5 flex items-center gap-0.5">
          <Clock className="h-2.5 w-2.5" />
          {d.durationMs < 1000 ? `${d.durationMs}ms` : `${(d.durationMs / 1000).toFixed(1)}s`}
        </div>
      )}
    </div>
  );
}

const NODE_TYPES = { custom: DnaNode };

// ── Node Inspector Panel ──────────────────────────────────────────────────────

function NodeInspector({
  node,
  onClose,
}: {
  node: Node | null;
  onClose: () => void;
}) {
  if (!node) return null;
  const d = node.data as unknown as DnaNodeData;
  const typeKey = (d.nodeType === 'tool' && d.status === 'failed') ? 'failed' : (d.nodeType || 'step');
  const style = NODE_STYLES[typeKey] ?? NODE_STYLES.step;
  const Icon = style.icon;

  return (
    <div className="absolute top-4 right-4 w-72 bg-card border border-border rounded-2xl shadow-2xl overflow-hidden z-50">
      {/* Header */}
      <div className={`flex items-center justify-between px-4 py-3 border-b border-border ${style.bg}`}>
        <div className="flex items-center gap-2">
          <Icon className={`h-4 w-4 ${style.iconColor}`} />
          <span className={`font-semibold text-sm ${style.text}`}>{d.label}</span>
        </div>
        <button onClick={onClose} className="p-1 rounded hover:bg-black/10 transition-colors">
          <X className="h-3.5 w-3.5 text-muted-foreground" />
        </button>
      </div>

      {/* Details */}
      <div className="p-4 space-y-3 text-xs">
        {/* Node type badge */}
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground">Type</span>
          <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium border ${style.bg} ${style.border} ${style.text} capitalize`}>
            {d.nodeType}
          </span>
        </div>

        {/* Node ID */}
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground">ID</span>
          <span className="font-mono text-[10px] bg-muted px-1.5 py-0.5 rounded max-w-[150px] truncate">{node.id}</span>
        </div>

        {/* Tool name */}
        {d.toolName && (
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Tool</span>
            <span className="font-mono font-medium">{d.toolName}</span>
          </div>
        )}

        {/* Server */}
        {d.serverId && (
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Server</span>
            <span className="font-mono">{d.serverId}</span>
          </div>
        )}

        {/* Status */}
        {d.status && (
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Status</span>
            <span className={`capitalize font-medium ${d.status === 'failed' ? 'text-red-600' : d.status === 'success' ? 'text-green-600' : 'text-blue-600'}`}>
              {d.status}
            </span>
          </div>
        )}

        {/* Duration */}
        {d.durationMs !== undefined && d.durationMs !== null && (
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Duration</span>
            <span className="font-mono font-medium">
              {d.durationMs < 1000 ? `${d.durationMs}ms` : `${(d.durationMs / 1000).toFixed(2)}s`}
            </span>
          </div>
        )}

        {/* Description */}
        {d.description && (
          <div className="space-y-1">
            <span className="text-muted-foreground">Description</span>
            <p className="text-foreground/80 bg-muted/50 rounded-lg p-2 leading-relaxed">{d.description}</p>
          </div>
        )}

        {/* Output preview */}
        {d.outputPreview && (
          <div className="space-y-1">
            <span className="text-muted-foreground flex items-center gap-1">
              <Code2 className="h-3 w-3" /> Output preview
            </span>
            <pre className="text-[10px] font-mono bg-muted rounded-lg p-2 overflow-auto max-h-24 whitespace-pre-wrap break-all">
              {d.outputPreview}
            </pre>
          </div>
        )}

        {/* Error */}
        {d.error && (
          <div className="space-y-1">
            <span className="text-red-500 flex items-center gap-1">
              <AlertCircle className="h-3 w-3" /> Error
            </span>
            <p className="text-red-600 bg-red-50 rounded-lg p-2 leading-relaxed">{d.error}</p>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Legend ────────────────────────────────────────────────────────────────────

function Legend() {
  const items = [
    { type: 'start', label: 'Start' },
    { type: 'step', label: 'Step' },
    { type: 'tool', label: 'Tool call' },
    { type: 'end', label: 'Complete' },
    { type: 'failed', label: 'Failed' },
  ] as const;

  return (
    <div className="flex items-center gap-3 flex-wrap">
      {items.map(({ type, label }) => {
        const s = NODE_STYLES[type];
        const Icon = s.icon;
        return (
          <div key={type} className="flex items-center gap-1 text-xs">
            <div className={`w-5 h-5 rounded border flex items-center justify-center ${s.bg} ${s.border}`}>
              <Icon className={`h-3 w-3 ${s.iconColor}`} />
            </div>
            <span className="text-muted-foreground">{label}</span>
          </div>
        );
      })}
    </div>
  );
}

// ── Toolbar inside the graph ──────────────────────────────────────────────────

function GraphToolbar({
  onExport,
  onRefresh,
  isRefreshing,
}: {
  onExport: () => void;
  onRefresh: () => void;
  isRefreshing: boolean;
}) {
  const { fitView, zoomIn, zoomOut } = useReactFlow();

  return (
    <Panel position="top-left">
      <div className="flex flex-col gap-1 bg-card/90 backdrop-blur-sm border border-border rounded-xl shadow-md p-1">
        <button
          onClick={() => zoomIn()}
          className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-muted transition-colors"
          title="Zoom in (+)"
        >
          <Plus className="h-4 w-4 text-muted-foreground" />
        </button>
        <button
          onClick={() => zoomOut()}
          className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-muted transition-colors"
          title="Zoom out (-)"
        >
          <Minus className="h-4 w-4 text-muted-foreground" />
        </button>
        <button
          onClick={() => fitView({ padding: 0.15, duration: 400 })}
          className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-muted transition-colors"
          title="Fit view (F)"
        >
          <Maximize2 className="h-4 w-4 text-muted-foreground" />
        </button>
        <div className="h-px bg-border mx-1" />
        <button
          onClick={onRefresh}
          className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-muted transition-colors"
          title="Refresh"
        >
          <RefreshCw className={`h-4 w-4 text-muted-foreground ${isRefreshing ? 'animate-spin' : ''}`} />
        </button>
        <button
          onClick={onExport}
          className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-muted transition-colors"
          title="Export as PNG"
        >
          <Download className="h-4 w-4 text-muted-foreground" />
        </button>
      </div>
    </Panel>
  );
}

// ── Error Boundary ────────────────────────────────────────────────────────────

class GraphErrorBoundary extends Component<
  { children: ReactNode },
  { hasError: boolean; error: string }
> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { hasError: false, error: '' };
  }
  static getDerivedStateFromError(err: Error) {
    return { hasError: true, error: err.message };
  }
  componentDidCatch(err: Error, info: ErrorInfo) {
    console.error('GoalDNA graph error:', err, info);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="flex flex-col items-center justify-center h-full gap-3 text-muted-foreground">
          <AlertCircle className="h-8 w-8 text-destructive/60" />
          <p className="text-sm font-medium">Graph render error</p>
          <p className="text-xs">{this.state.error}</p>
          <button
            onClick={() => this.setState({ hasError: false, error: '' })}
            className="text-xs px-3 py-1.5 bg-primary text-primary-foreground rounded-md"
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

// ── Inner graph (needs ReactFlowProvider) ─────────────────────────────────────

function InnerGraph({
  nodes,
  edges,
  onNodeClick,
  goalId,
  onRefresh,
  isRefreshing,
}: {
  nodes: Node[];
  edges: Edge[];
  onNodeClick: (node: Node) => void;
  goalId: string | undefined;
  onRefresh: () => void;
  isRefreshing: boolean;
}) {
  const { fitView, getNodes } = useReactFlow();

  // Fit on mount whenever nodes change
  useEffect(() => {
    const t = setTimeout(() => fitView({ padding: 0.15, duration: 400 }), 100);
    return () => clearTimeout(t);
  }, [nodes.length, fitView]);

  // Keyboard shortcut: F = fitView
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'f' || e.key === 'F') fitView({ padding: 0.15, duration: 400 });
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [fitView]);

  const handleExport = useCallback(async () => {
    try {
      const flowElement = document.querySelector('.react-flow__viewport') as HTMLElement | null;
      if (!flowElement) throw new Error('Flow element not found');

      const htmlToImage = await import('html-to-image').catch(() => null);
      if (htmlToImage) {
        const rfNodes = getNodes();
        const nodesBounds = getNodesBounds(rfNodes);
        const padding = 40;
        const imageWidth = Math.max(nodesBounds.width + padding * 2, 800);
        const imageHeight = Math.max(nodesBounds.height + padding * 2, 600);

        const dataUrl = await htmlToImage.toPng(flowElement, {
          backgroundColor: '#ffffff',
          width: imageWidth,
          height: imageHeight,
          style: { transform: `translate(${padding}px, ${padding}px)` },
        });

        const a = document.createElement('a');
        a.href = dataUrl;
        a.download = `goal-dna-${goalId?.slice(0, 8) ?? 'export'}.png`;
        a.click();
        toast({ kind: 'success', message: 'Graph exported as PNG' });
        return;
      }
    } catch (err) {
      console.warn('PNG export failed, falling back to JSON download:', err);
    }

    // Fallback: inform user
    toast({ kind: 'info', message: 'PNG export unavailable — install html-to-image or use a screenshot' });
  }, [goalId, getNodes]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={NODE_TYPES}
      onNodeClick={(_e, node) => onNodeClick(node)}
      fitView
      fitViewOptions={{ padding: 0.15 }}
      minZoom={0.1}
      maxZoom={3}
      snapToGrid
      snapGrid={[16, 16]}
      proOptions={{ hideAttribution: true }}
      defaultEdgeOptions={{
        type: 'smoothstep',
        markerEnd: { type: MarkerType.ArrowClosed, width: 12, height: 12, color: '#94a3b8' },
        style: { stroke: '#94a3b8', strokeWidth: 1.5 },
      }}
    >
      <Background variant={BackgroundVariant.Dots} gap={20} size={1} className="opacity-40" />
      <MiniMap
        style={{ background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: '12px' }}
        nodeColor={(n) => {
          const d = n.data as unknown as DnaNodeData;
          const t = (d.nodeType === 'tool' && d.status === 'failed') ? 'failed' : (d.nodeType || 'step');
          const map: Record<string, string> = {
            start: '#3b82f6', step: '#8b5cf6', tool: '#f59e0b', end: '#22c55e', failed: '#ef4444',
          };
          return map[t] ?? '#94a3b8';
        }}
        pannable
        zoomable
      />
      <GraphToolbar onExport={() => void handleExport()} onRefresh={onRefresh} isRefreshing={isRefreshing} />
      {/* Hide the default Controls (we have our own toolbar) */}
    </ReactFlow>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export function GoalDNAPage() {
  const { goalId } = useParams<{ goalId: string }>();
  const navigate = useNavigate();
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [showTimeline, setShowTimeline] = useState(false);

  // Separate lightweight query to track goal status for auto-refresh
  const { data: goalData } = useQuery({
    queryKey: ['goal', goalId],
    queryFn: () => goalsApi.get(goalId!),
    enabled: !!goalId,
    refetchInterval: 5_000,
    staleTime: 5_000,
  });

  const {
    data: graph,
    isLoading,
    isError,
    refetch,
    isFetching,
  } = useQuery({
    queryKey: ['goal-dna', goalId],
    queryFn: () => insightsApi.getExecutionGraph(goalId!),
    enabled: !!goalId,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
    // Auto-refresh while goal is executing or planning
    refetchInterval: goalData && ['executing', 'planning'].includes(goalData.status) ? 5_000 : false,
  });

  // Build ReactFlow nodes + edges from the API response
  const { nodes, edges } = useMemo<{ nodes: Node<DnaNodeData>[]; edges: Edge[] }>(() => {
    if (!graph) return { nodes: [], edges: [] };

    const nodeInputs: FlowNodeInput[] = graph.nodes.map((n) => ({
      id: n.id,
      label: n.label,
      kind: n.type,
      data: n.data,
    }));
    const edgeInputs: FlowEdgeInput[] = graph.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
    }));

    const positions = layeredLayout(nodeInputs, edgeInputs);

    const flowNodes: Node<DnaNodeData>[] = graph.nodes.map((n) => ({
      id: n.id,
      type: 'custom',
      position: positions[n.id] ?? { x: 0, y: 0 },
      draggable: true,
      selectable: true,
      data: {
        label: n.label,
        nodeType: n.type as DnaNodeData['nodeType'],
        ...n.data,
        toolName: ((n.data as Record<string, unknown>)?.tool_name ?? (n.data as Record<string, unknown>)?.toolName) as string | undefined,
        serverId: ((n.data as Record<string, unknown>)?.server_id ?? (n.data as Record<string, unknown>)?.serverId) as string | undefined,
        outputPreview: ((n.data as Record<string, unknown>)?.output_preview) as string | undefined,
        durationMs: ((n.data as Record<string, unknown>)?.duration_ms) as number | undefined,
        error: ((n.data as Record<string, unknown>)?.error) as string | undefined,
      } as DnaNodeData,
    }));

    // Animate edges connected to tool nodes
    const flowEdges: Edge[] = graph.edges.map((e) => {
      const targetNode = graph.nodes.find((n) => n.id === e.target);
      const isToolEdge = targetNode?.type === 'tool';
      const isFailedEdge = targetNode?.type === 'failed' || ((targetNode?.data as Record<string, unknown>)?.status as string) === 'failed';
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        type: 'smoothstep',
        animated: isToolEdge,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 12,
          height: 12,
          color: isFailedEdge ? '#ef4444' : '#94a3b8',
        },
        style: {
          stroke: isFailedEdge ? '#ef4444' : isToolEdge ? '#f59e0b' : '#94a3b8',
          strokeWidth: 1.5,
          strokeDasharray: isFailedEdge ? '4 2' : undefined,
        },
      };
    });

    return { nodes: flowNodes, edges: flowEdges };
  }, [graph]);

  const handleNodeClick = useCallback((node: Node) => {
    setSelectedNode((prev) => (prev?.id === node.id ? null : node));
  }, []);

  const stats = graph?.stats;
  const toolNodes = graph?.nodes.filter((n) => n.type === 'tool') ?? [];
  const failedTools = toolNodes.filter((n) => (n.data as Record<string, unknown>)?.status === 'failed').length;

  return (
    <div className="flex flex-col h-screen bg-background">
      {/* ── Top header ───────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-border bg-card/80 backdrop-blur-sm shrink-0">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate(`/goals/${goalId}`)}
            className="p-1.5 rounded-lg hover:bg-muted transition-colors"
            title="Back to goal"
          >
            <ArrowLeft className="h-4 w-4 text-muted-foreground" />
          </button>
          <div>
            <h1 className="text-base font-bold flex items-center gap-2">
              <Target className="h-4 w-4 text-violet-500" />
              Goal DNA
            </h1>
            <p className="text-xs text-muted-foreground font-mono mt-0.5">
              Execution graph · <span className="text-foreground/70">{goalId?.slice(0, 16)}…</span>
            </p>
          </div>
        </div>

        {/* Stats strip */}
        <div className="flex items-center gap-4">
          {stats && (
            <>
              <StatPill icon={<Layers className="h-3.5 w-3.5 text-violet-500" />} label="nodes" value={stats.total_nodes} />
              <StatPill icon={<Wrench className="h-3.5 w-3.5 text-amber-500" />} label="tool calls" value={stats.tool_calls} />
              <StatPill icon={<GitBranch className="h-3.5 w-3.5 text-blue-500" />} label="unique tools" value={stats.unique_tools} />
              {failedTools > 0 && (
                <StatPill icon={<XCircle className="h-3.5 w-3.5 text-red-500" />} label="failed" value={failedTools} danger />
              )}
            </>
          )}
          <button
            onClick={() => setShowTimeline((v) => !v)}
            className={`flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border transition-colors ${
              showTimeline ? 'bg-primary text-primary-foreground border-primary' : 'border-border hover:bg-muted'
            }`}
          >
            <Clock className="h-3.5 w-3.5" />
            Timeline
          </button>
          <button
            onClick={() => void refetch()}
            disabled={isFetching}
            className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border border-border hover:bg-muted transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* ── Body ─────────────────────────────────────────────────────────── */}
      <div className="flex flex-1 min-h-0 overflow-hidden">

        {/* ── Timeline sidebar ─────────────────────────────────────────── */}
        {showTimeline && graph && (
          <div className="w-56 border-r border-border bg-card/50 overflow-y-auto shrink-0">
            <div className="p-3 border-b border-border">
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Execution Timeline</p>
            </div>
            <div className="p-2 space-y-1">
              {graph.nodes.map((n, i) => {
                const typeKey = (n.type === 'tool' && (n.data as Record<string, unknown>)?.status === 'failed') ? 'failed' : n.type;
                const s = NODE_STYLES[typeKey as keyof typeof NODE_STYLES] ?? NODE_STYLES.step;
                const Icon = s.icon;
                return (
                  <div key={n.id} className="flex items-start gap-2">
                    <div className="relative flex flex-col items-center">
                      <div className={`w-5 h-5 rounded-full border flex items-center justify-center shrink-0 ${s.bg} ${s.border}`}>
                        <Icon className={`h-2.5 w-2.5 ${s.iconColor}`} />
                      </div>
                      {i < graph.nodes.length - 1 && (
                        <div className="w-px flex-1 bg-border mt-0.5 min-h-[12px]" />
                      )}
                    </div>
                    <div className="pb-2 min-w-0">
                      <p className={`text-[10px] font-medium truncate ${s.text}`}>{n.label}</p>
                      {Boolean((n.data as Record<string, unknown>)?.tool_name) && (
                        <p className="text-[9px] text-muted-foreground font-mono truncate">{((n.data as Record<string, unknown>).tool_name as string) || ''}</p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* ── Graph canvas ─────────────────────────────────────────────── */}
        <div className="flex-1 flex flex-col min-h-0 overflow-hidden relative">

          {/* Legend bar */}
          <div className="flex items-center justify-between px-4 py-2 border-b border-border bg-card/30 shrink-0">
            <Legend />
            <p className="text-[10px] text-muted-foreground hidden sm:block">
              Click a node for details · Scroll to zoom · Drag to pan · F to fit
            </p>
          </div>

          {/* The graph itself */}
          <div className="flex-1 relative min-h-0">
            {isLoading && (
              <div className="absolute inset-0 flex flex-col p-6 gap-3">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-16 w-full rounded-xl" />
                ))}
              </div>
            )}

            {isError && !isLoading && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-muted-foreground">
                <Info className="h-8 w-8 opacity-40" />
                <p className="text-sm font-medium">Could not load execution graph</p>
                <button
                  onClick={() => void refetch()}
                  className="text-xs px-3 py-1.5 bg-primary text-primary-foreground rounded-md"
                >
                  Retry
                </button>
              </div>
            )}

            {!isLoading && !isError && graph && nodes.length === 0 && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 text-muted-foreground">
                <div className="w-16 h-16 rounded-2xl bg-muted flex items-center justify-center">
                  <GitBranch className="h-8 w-8 opacity-40" />
                </div>
                <div className="text-center">
                  <p className="text-sm font-medium">No execution events yet</p>
                  <p className="text-xs mt-1 opacity-70">Run the goal to see the execution DNA</p>
                </div>
              </div>
            )}

            {!isLoading && !isError && nodes.length > 0 && (
                <GraphErrorBoundary>
                  <ReactFlowProvider>
                    <InnerGraph
                      nodes={nodes}
                      edges={edges}
                      onNodeClick={handleNodeClick}
                      goalId={goalId}
                      onRefresh={() => void refetch()}
                      isRefreshing={isFetching}
                    />
                  </ReactFlowProvider>
                </GraphErrorBoundary>
            )}
          </div>

          {/* Node inspector panel (overlaid on the graph) */}
          {selectedNode && (
            <NodeInspector node={selectedNode} onClose={() => setSelectedNode(null)} />
          )}

        </div>
      </div>
    </div>
  );
}

// ── Small helper component ───────────────────────────────────────────────────

function StatPill({
  icon,
  label,
  value,
  danger = false,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
  danger?: boolean;
}) {
  return (
    <div className={`flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border ${danger ? 'bg-red-50 border-red-200 text-red-700' : 'bg-muted border-border'}`}>
      {icon}
      <span className="font-bold">{value}</span>
      <span className={danger ? 'text-red-500' : 'text-muted-foreground'}>{label}</span>
    </div>
  );
}
