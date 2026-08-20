/**
 * CivilizationMap — React Flow canvas with world-class dark theme.
 *
 * Key UX improvements:
 * - Constrained zoom (0.3–1.8) to avoid disorienting zoom-out
 * - panOnScroll enabled for trackpad users
 * - fitView with generous padding
 * - Styled dark background grid
 * - Polished minimap with dark skin
 * - Animated edges for live bus messages
 * - Empty state with call-to-action
 */
import { useCallback, useEffect, useMemo, useRef } from 'react';
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  useReactFlow,
  ReactFlowProvider,
  type Node,
  type Edge,
  type NodeMouseHandler,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { AgentNode } from './AgentNode';
import type { SocietyNode, SocietyEdge, CivilizationEvent } from '../../lib/api/civilizationApi';

interface CivilizationMapProps {
  nodes: SocietyNode[];
  edges: SocietyEdge[];
  onNodeClick?: (agentId: string) => void;
  liveEvents: CivilizationEvent[];
}

const NODE_TYPES = { agent: AgentNode };

/** Radial/tree layout that prevents node overlap */
function layoutNodes(nodes: SocietyNode[]): Node[] {
  if (nodes.length === 0) return [];

  const depthGroups: Record<number, SocietyNode[]> = {};
  for (const n of nodes) {
    const d = n.depth ?? 0;
    (depthGroups[d] = depthGroups[d] || []).push(n);
  }

  const result: Node[] = [];
  const xSpacing = 230;
  const ySpacing = 180;

  for (const [depthStr, group] of Object.entries(depthGroups)) {
    const depth = parseInt(depthStr, 10);
    const totalW = (group.length - 1) * xSpacing;
    const startX = -totalW / 2;
    group.forEach((n, i) => {
      result.push({
        id: n.id,
        type: 'agent',
        position: { x: startX + i * xSpacing, y: depth * ySpacing },
        data: {
          label: n.label || n.id.slice(0, 10),
          status: n.status ?? 'idle',
          reputation: n.reputation ?? 0.5,
          depth: n.depth ?? 0,
          budget_spent_usd: n.budget_spent_usd,
        },
      });
    });
  }
  return result;
}

const EDGE_COLORS: Record<string, string> = {
  spawn_lineage: '#6366f1',
  debate: '#a855f7',
  findings: '#22c55e',
  bus_message: '#f59e0b',
};

function buildEdges(rawEdges: SocietyEdge[]): Edge[] {
  return rawEdges
    .filter(e => e.source && e.target && e.source !== 'bus' && e.target !== 'bus')
    .map((e, i) => {
      const color = EDGE_COLORS[e.type ?? ''] ?? EDGE_COLORS[e.topic ?? ''] ?? '#475569';
      const isLive = e.type === 'bus_message';
      return {
        id: `edge-${i}-${e.source}-${e.target}`,
        source: e.source,
        target: e.target,
        type: 'smoothstep',
        animated: isLive,
        style: {
          stroke: color,
          strokeWidth: e.type === 'spawn_lineage' ? 2.5 : 1.5,
          strokeDasharray: isLive ? '6 3' : undefined,
          opacity: 0.8,
        },
        labelStyle: { fontSize: 10, fill: '#94a3b8' },
        label: e.topic && e.topic !== 'bus_message' ? e.topic : undefined,
        markerEnd: {
          type: 'arrowclosed' as const,
          color,
          width: 16,
          height: 16,
        },
      };
    });
}

/** Inner component that has access to ReactFlow context */
function MapInner({
  nodes,
  edges,
  onNodeClick,
  liveEvents: _liveEvents,
}: CivilizationMapProps) {
  const { fitView } = useReactFlow();
  const prevNodeCount = useRef(0);

  const flowNodes = useMemo(() => layoutNodes(nodes), [nodes]);
  const flowEdges = useMemo(() => buildEdges(edges), [edges]);

  const [rfNodes, setRfNodes, onNodesChange] = useNodesState(flowNodes);
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState(flowEdges);

  // Sync nodes
  useEffect(() => {
    setRfNodes(layoutNodes(nodes));
    // Auto-fit when new nodes appear
    if (nodes.length !== prevNodeCount.current) {
      prevNodeCount.current = nodes.length;
      setTimeout(() => fitView({ padding: 0.25, duration: 600 }), 100);
    }
  }, [nodes, setRfNodes, fitView]);

  useEffect(() => {
    setRfEdges(buildEdges(edges));
  }, [edges, setRfEdges]);

  const handleNodeClick = useCallback<NodeMouseHandler>(
    (_, node) => onNodeClick?.(node.id),
    [onNodeClick]
  );

  if (nodes.length === 0) {
    return (
      <div
        className="h-full flex items-center justify-center"
        style={{ background: 'linear-gradient(135deg, #0f172a 0%, #1e293b 100%)' }}
      >
        <div className="text-center space-y-4">
          <div
            className="w-20 h-20 rounded-2xl mx-auto flex items-center justify-center text-4xl"
            style={{ background: 'rgba(99,102,241,0.1)', border: '1px solid rgba(99,102,241,0.2)' }}
          >
            🌐
          </div>
          <div>
            <p className="text-slate-200 font-semibold text-lg">Society is empty</p>
            <p className="text-[#5A7494] text-sm mt-1">
              Submit a goal above to spawn the first agent
            </p>
          </div>
          <div className="flex items-center justify-center gap-2 text-xs text-slate-600">
            <span className="w-2 h-2 rounded-full bg-indigo-500" />
            <span>spawn_lineage</span>
            <span className="w-2 h-2 rounded-full bg-purple-500 ml-2" />
            <span>debate</span>
            <span className="w-2 h-2 rounded-full bg-green-500 ml-2" />
            <span>findings</span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <ReactFlow
      nodes={rfNodes}
      edges={rfEdges}
      nodeTypes={NODE_TYPES}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={handleNodeClick}
      fitView
      fitViewOptions={{ padding: 0.3, duration: 800 }}
      minZoom={0.2}
      maxZoom={2}
      panOnScroll
      zoomOnDoubleClick={false}
      attributionPosition="bottom-right"
      style={{ background: 'linear-gradient(135deg, #0f172a 0%, #0d1625 100%)' }}
      proOptions={{ hideAttribution: true }}
    >
      {/* Dot grid background */}
      <Background
        variant={BackgroundVariant.Dots}
        gap={28}
        size={1}
        color="rgba(148,163,184,0.12)"
      />

      {/* Compact dark controls */}
      <Controls
        style={{
          background: 'rgba(15,23,42,0.9)',
          border: '1px solid rgba(255,255,255,0.08)',
          borderRadius: '12px',
          boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
        }}
      />

      {/* Dark minimap */}
      <MiniMap
        nodeColor={(node) => {
          const status = (node.data as { status?: string }).status ?? 'idle';
          const colors: Record<string, string> = {
            active: '#3b82f6',
            debating: '#a855f7',
            spawning: '#f59e0b',
            idle: '#475569',
            retired: '#334155',
            failed: '#ef4444',
          };
          return colors[status] ?? '#475569';
        }}
        style={{
          background: 'rgba(15,23,42,0.95)',
          border: '1px solid rgba(255,255,255,0.08)',
          borderRadius: '12px',
          overflow: 'hidden',
        }}
        maskColor="rgba(0,0,0,0.4)"
        pannable
        zoomable
      />
    </ReactFlow>
  );
}

/** Wrap with ReactFlowProvider so fitView works */
export function CivilizationMap(props: CivilizationMapProps) {
  return (
    <div className="relative w-full h-full">
      <ReactFlowProvider>
        <MapInner {...props} />
      </ReactFlowProvider>
    </div>
  );
}
