/**
 * FlowCanvas — shared @xyflow/react wrapper used by WorkflowBuilder and CivilizationMap.
 *
 * Two usage modes:
 *  1. Simple: pass `FlowNodeInput[]` + `FlowEdgeInput[]` — layeredLayout positions them automatically.
 *  2. Full:   pass raw `Node[]` + `Edge[]` with pre-assigned positions and change handlers.
 *
 * Phase 1 delivers mode 1 (auto-layout). Phase 6 (Workflow Builder) upgrades to mode 2.
 */
import { useMemo } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  BackgroundVariant,
  type Node,
  type Edge,
  type OnNodesChange,
  type OnEdgesChange,
  type OnConnect,
  type NodeTypes,
  type EdgeTypes,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

// ---------------------------------------------------------------------------
// Simple input types (Phase 1 / auto-layout mode)
// ---------------------------------------------------------------------------

export interface FlowNodeInput {
  id: string;
  label: string;
  kind?: string;
  data?: Record<string, unknown>;
}

export interface FlowEdgeInput {
  id: string;
  source: string;
  target: string;
  label?: string;
}

const X_GAP = 220;
const Y_GAP = 110;

/**
 * Assign positions by BFS depth from roots (nodes with no incoming edge).
 * 220 px x-gap per depth level, 110 px y-gap per sibling at the same depth.
 */
export function layeredLayout(
  nodes: FlowNodeInput[],
  edges: FlowEdgeInput[],
): Record<string, { x: number; y: number }> {
  const incoming = new Set(edges.map((e) => e.target));
  const children = new Map<string, string[]>();
  for (const e of edges) {
    children.set(e.source, [...(children.get(e.source) ?? []), e.target]);
  }

  const depth = new Map<string, number>();
  const roots = nodes.filter((n) => !incoming.has(n.id)).map((n) => n.id);
  const queue = roots.map((id) => ({ id, d: 0 }));
  const seen = new Set<string>();

  while (queue.length) {
    const { id, d } = queue.shift()!;
    if (seen.has(id)) continue;
    seen.add(id);
    depth.set(id, Math.max(depth.get(id) ?? 0, d));
    for (const c of children.get(id) ?? []) queue.push({ id: c, d: d + 1 });
  }

  // Any node never reached (cycle/orphan) gets depth 0.
  for (const n of nodes) {
    if (!depth.has(n.id)) depth.set(n.id, 0);
  }

  const perDepthCount = new Map<number, number>();
  const pos: Record<string, { x: number; y: number }> = {};

  for (const n of nodes) {
    const d = depth.get(n.id) ?? 0;
    const row = perDepthCount.get(d) ?? 0;
    perDepthCount.set(d, row + 1);
    pos[n.id] = { x: d * X_GAP, y: row * Y_GAP };
  }

  return pos;
}

// ---------------------------------------------------------------------------
// Props — supports both simple and full (controlled) modes
// ---------------------------------------------------------------------------

interface SimpleFlowCanvasProps {
  /** Simple mode: auto-positioned nodes */
  nodes: FlowNodeInput[];
  edges: FlowEdgeInput[];
  onNodeClick?: (id: string) => void;
  nodeColor?: (n: FlowNodeInput) => string;
  // Full-mode props (optional — ignored in simple mode)
  onNodesChange?: never;
  onEdgesChange?: never;
  onConnect?: never;
  nodeTypes?: never;
  edgeTypes?: never;
  fitView?: boolean;
  snapToGrid?: boolean;
  className?: string;
  children?: React.ReactNode;
}

interface ControlledFlowCanvasProps {
  /** Full / controlled mode: caller manages Node<>/Edge<> objects */
  nodes: Node[];
  edges: Edge[];
  onNodesChange: OnNodesChange;
  onEdgesChange: OnEdgesChange;
  onConnect?: OnConnect;
  onNodeClick?: (event: React.MouseEvent, node: Node) => void;
  nodeTypes?: NodeTypes;
  edgeTypes?: EdgeTypes;
  fitView?: boolean;
  snapToGrid?: boolean;
  className?: string;
  children?: React.ReactNode;
  // Simple-mode-only props (not used)
  nodeColor?: never;
}

type FlowCanvasProps = SimpleFlowCanvasProps | ControlledFlowCanvasProps;

function isControlled(p: FlowCanvasProps): p is ControlledFlowCanvasProps {
  return typeof (p as ControlledFlowCanvasProps).onNodesChange === 'function';
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function FlowCanvas(props: FlowCanvasProps) {
  const {
    fitView = true,
    snapToGrid = true,
    className,
    children,
  } = props;

  // Simple mode: convert FlowNodeInput[] → Node[], FlowEdgeInput[] → Edge[]
  const simpleNodes = props.nodes as FlowNodeInput[];
  const simpleEdges = props.edges as FlowEdgeInput[];

  const pos = useMemo(
    () => (!isControlled(props) ? layeredLayout(simpleNodes, simpleEdges) : {}),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [props.nodes, props.edges],
  );

  const rfNodes: Node[] = useMemo(() => {
    if (isControlled(props)) return props.nodes as Node[];
    const { nodeColor } = props as SimpleFlowCanvasProps;
    return simpleNodes.map((n) => ({
      id: n.id,
      position: pos[n.id] ?? { x: 0, y: 0 },
      data: { label: n.label, ...n.data },
      style: nodeColor ? { borderColor: nodeColor(n) } : undefined,
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.nodes, pos]);

  const rfEdges: Edge[] = useMemo(() => {
    if (isControlled(props)) return props.edges as Edge[];
    return simpleEdges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      label: e.label,
      animated: true,
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.edges]);

  const handleNodeClick = isControlled(props)
    ? (props as ControlledFlowCanvasProps).onNodeClick
    : (_event: React.MouseEvent, node: Node) =>
        (props as SimpleFlowCanvasProps).onNodeClick?.(node.id);

  return (
    <div className={`w-full h-full min-h-[400px] ${className ?? ''}`} data-testid="flow-canvas">
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        onNodesChange={isControlled(props) ? props.onNodesChange : undefined}
        onEdgesChange={isControlled(props) ? props.onEdgesChange : undefined}
        onConnect={isControlled(props) ? props.onConnect : undefined}
        onNodeClick={handleNodeClick}
        nodeTypes={isControlled(props) ? props.nodeTypes : undefined}
        edgeTypes={isControlled(props) ? props.edgeTypes : undefined}
        fitView={fitView}
        snapToGrid={snapToGrid}
        snapGrid={[16, 16]}
        attributionPosition="bottom-right"
        deleteKeyCode="Delete"
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
        <Controls />
        <MiniMap style={{ background: '#f8fafc' }} pannable zoomable />
        {children}
      </ReactFlow>
    </div>
  );
}

export default FlowCanvas;
