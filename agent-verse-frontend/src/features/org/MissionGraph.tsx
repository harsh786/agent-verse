/**
 * MissionGraph — visual DAG of mission task dependencies.
 *
 * JARVIS motion system:
 *   - JARVISPageShell:  blur-in entry
 *   - Animated edges:   draw-on with spring
 *   - Node status dots: live status colours
 */
import { useMemo } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  MarkerType,
  type Edge,
  type Node,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { CheckCircle2, Loader2, Clock, AlertTriangle, Circle } from 'lucide-react';
import { cn } from '@/lib/utils';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { useOrgTasks } from './hooks/useOrg';
import type { OrgTask, TaskStatus } from './types';

// ── Node status config ─────────────────────────────────────────────────────

const STATUS_NODE: Record<TaskStatus, { bg: string; border: string; icon: React.ElementType; iconClass: string }> = {
  draft:              { bg: '#0F1623', border: '#1E2535', icon: Circle,        iconClass: 'text-[#475569]'  },
  queued:             { bg: '#0F1623', border: '#1E2535', icon: Clock,         iconClass: 'text-yellow-400' },
  planned:            { bg: '#0F1623', border: '#2563EB', icon: Clock,         iconClass: 'text-blue-400'   },
  assigned:           { bg: '#0F1623', border: '#6366F1', icon: Loader2,       iconClass: 'text-indigo-400' },
  running:            { bg: '#0F1623', border: '#34D399', icon: Loader2,       iconClass: 'text-emerald-400 animate-spin' },
  waiting:            { bg: '#0F1623', border: '#FBB724', icon: Clock,         iconClass: 'text-yellow-400' },
  blocked:            { bg: '#0F1623', border: '#F87171', icon: AlertTriangle, iconClass: 'text-red-400'    },
  review:             { bg: '#0F1623', border: '#818CF8', icon: Clock,         iconClass: 'text-indigo-400' },
  approval_required:  { bg: '#0F1623', border: '#F97316', icon: AlertTriangle, iconClass: 'text-orange-400' },
  completed:          { bg: '#0F1623', border: '#10B981', icon: CheckCircle2,  iconClass: 'text-emerald-500' },
  failed:             { bg: '#0F1623', border: '#EF4444', icon: AlertTriangle, iconClass: 'text-red-500'    },
  cancelled:          { bg: '#0F1623', border: '#475569', icon: Circle,        iconClass: 'text-[#475569]'  },
  expired:            { bg: '#0F1623', border: '#475569', icon: Clock,         iconClass: 'text-[#475569]'  },
  archived:           { bg: '#0F1623', border: '#1E2535', icon: Circle,        iconClass: 'text-[#1E2535]'  },
};

// ── Task node ──────────────────────────────────────────────────────────────

function TaskNode({ data }: { data: { task: OrgTask } }) {
  const { task } = data;
  const conf     = STATUS_NODE[task.status] ?? STATUS_NODE.draft;
  const Icon     = conf.icon;

  return (
    <div
      className="rounded-xl px-3 py-2.5 min-w-[140px] max-w-[180px]"
      style={{ background: conf.bg, border: `1.5px solid ${conf.border}` }}
      role="article"
      aria-label={`Task: ${(task as any).title}`}
    >
      <Handle type="target" position={Position.Top}    style={{ background: conf.border, border: 'none' }} />
      <div className="flex items-start gap-2">
        <Icon className={cn('h-3.5 w-3.5 flex-shrink-0 mt-0.5', conf.iconClass)} aria-hidden />
        <p className="text-[11px] font-medium text-[#F1F5F9] leading-tight line-clamp-2">
          {(task as any).title ?? 'Untitled'}
        </p>
      </div>
      <p className="text-[9px] text-[#475569] mt-1 capitalize font-mono">{task.status}</p>
      <Handle type="source" position={Position.Bottom} style={{ background: conf.border, border: 'none' }} />
    </div>
  );
}

const NODE_TYPES = { task: TaskNode };

// ── Build DAG from tasks ───────────────────────────────────────────────────

function buildDAG(tasks: OrgTask[]): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = tasks.map((task, i) => ({
    id: task.id,
    type: 'task',
    position: {
      x: (i % 4) * 220 + 40,
      y: Math.floor(i / 4) * 140 + 40,
    },
    data: { task },
  }));

  // Build edges from sequential dependencies (simplified — real deps from API)
  const edges: Edge[] = [];
  for (let i = 1; i < tasks.length; i++) {
    const prev = tasks[i - 1];
    const curr = tasks[i];
    if ((curr as any).depends_on?.includes(prev.id)) {
      edges.push({
        id: `e-${prev.id}-${curr.id}`,
        source: prev.id,
        target: curr.id,
        animated: curr.status === 'running',
        style: { stroke: '#1E2535', strokeWidth: 1.5 },
        markerEnd: { type: MarkerType.ArrowClosed, color: '#1E2535' },
      });
    }
  }

  return { nodes, edges };
}

// ── Main ───────────────────────────────────────────────────────────────────

interface MissionGraphProps {
  orgId: string;
  missionId: string;
  className?: string;
}

function MissionGraphInner({ orgId, missionId, className }: MissionGraphProps) {
  const { data: taskData, isLoading } = useOrgTasks(orgId, { mission_id: missionId });
  const tasks: OrgTask[] = (taskData as any)?.data ?? [];

  const { nodes: initNodes, edges: initEdges } = useMemo(
    () => buildDAG(tasks),
    [tasks],
  );

  const [nodes, , onNodesChange] = useNodesState(initNodes);
  const [edges, , onEdgesChange] = useEdgesState(initEdges);

  return (
    <JARVISPageShell className={cn('h-full', className)}>
      {isLoading ? (
        <div className="flex items-center justify-center h-full">
          <Loader2 className="h-5 w-5 animate-spin text-[#00D4FF]" aria-label="Loading task graph" />
        </div>
      ) : tasks.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-full gap-3">
          <Circle className="h-10 w-10 text-[#1E2535]" aria-hidden />
          <p className="text-sm text-[#475569]">No tasks in this mission yet.</p>
        </div>
      ) : (
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          fitView
          fitViewOptions={{ padding: 0.15 }}
          style={{ background: '#0A0D14', borderRadius: 12 }}
        >
          <Background color="#1E2535" gap={20} size={1} />
          <Controls style={{ background: '#0F1623', border: '1px solid #1E2535', borderRadius: 8 }} />
        </ReactFlow>
      )}
    </JARVISPageShell>
  );
}

export function MissionGraph(props: MissionGraphProps) {
  return (
    <ReactFlowProvider>
      <MissionGraphInner {...props} />
    </ReactFlowProvider>
  );
}

export default MissionGraph;
