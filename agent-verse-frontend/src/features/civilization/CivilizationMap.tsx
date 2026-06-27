/**
 * Civilization Map — centerpiece @xyflow/react live graph.
 * Nodes = agents, edges = spawn lineage + live bus messages.
 */
import { useCallback, useEffect, useMemo } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
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

function layoutNodes(nodes: SocietyNode[]): Node[] {
  // Simple depth-based tree layout
  const depthGroups: Record<number, SocietyNode[]> = {};
  for (const n of nodes) {
    (depthGroups[n.depth] = depthGroups[n.depth] || []).push(n);
  }

  const result: Node[] = [];
  const xSpacing = 220;
  const ySpacing = 150;

  for (const [depthStr, group] of Object.entries(depthGroups)) {
    const depth = parseInt(depthStr);
    const startX = -(group.length - 1) * xSpacing / 2;
    group.forEach((n, i) => {
      result.push({
        id: n.id,
        type: 'agent',
        position: { x: startX + i * xSpacing, y: depth * ySpacing },
        data: {
          label: n.label || n.id.slice(0, 12),
          status: n.status,
          reputation: n.reputation,
          depth: n.depth,
          budget_spent_usd: n.budget_spent_usd,
        },
      });
    });
  }
  return result;
}

function buildEdges(rawEdges: SocietyEdge[]): Edge[] {
  return rawEdges
    .filter(e => e.source !== 'bus' && e.target !== 'bus')
    .map((e, i) => ({
      id: `edge-${i}-${e.source}-${e.target}`,
      source: e.source,
      target: e.target,
      type: 'smoothstep',
      animated: e.type === 'bus_message',
      style: {
        stroke:
          e.type === 'spawn_lineage' ? '#6366f1' :
          e.topic === 'debate' ? '#a855f7' :
          e.topic === 'findings' ? '#22c55e' :
          '#94a3b8',
        strokeWidth: e.type === 'spawn_lineage' ? 2 : 1,
        strokeDasharray: e.type === 'bus_message' ? '4 2' : undefined,
      },
      label: e.topic,
    }));
}

export function CivilizationMap({ nodes, edges, onNodeClick, liveEvents: _liveEvents }: CivilizationMapProps) {
  const flowNodes = useMemo(() => layoutNodes(nodes), [nodes]);
  const flowEdges = useMemo(() => buildEdges(edges), [edges]);

  const [rfNodes, setRfNodes, onNodesChange] = useNodesState(flowNodes);
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState(flowEdges);

  // Sync when upstream data changes
  useEffect(() => {
    setRfNodes(layoutNodes(nodes));
  }, [nodes, setRfNodes]);

  useEffect(() => {
    setRfEdges(buildEdges(edges));
  }, [edges, setRfEdges]);

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      onNodeClick?.(node.id);
    },
    [onNodeClick]
  );

  if (nodes.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400">
        <div className="text-center">
          <div className="text-4xl mb-2">&#127760;</div>
          <div className="text-sm">No agents yet &mdash; submit a goal to spawn the first agent</div>
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
      attributionPosition="bottom-right"
    >
      <Background />
      <Controls />
      <MiniMap
        nodeColor={(node) => {
          const status = (node.data as { status?: string }).status ?? 'idle';
          const colors: Record<string, string> = {
            active: '#3b82f6', debating: '#a855f7', spawning: '#f59e0b',
            idle: '#94a3b8', retired: '#d1d5db', failed: '#ef4444',
          };
          return colors[status] ?? '#94a3b8';
        }}
        style={{ background: '#f8fafc' }}
      />
    </ReactFlow>
  );
}
