/**
 * OrgMap — live agent graph using React Flow with force layout.
 *
 * JARVIS motion system:
 *   - JARVISPageShell:  blur-in page entry
 *   - Node fly-in:      agents stagger in from their departments
 *   - Spring edges:     animated draw-on
 *   - Status colors:    AGENT_STATUS_COLOR per spec
 */
import { useEffect, useMemo, useState } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  MarkerType,
  type Node,
  type Edge,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Building2, User, Search, X, RefreshCw } from 'lucide-react';
import { cn } from '@/lib/utils';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { Input } from '@/components/ui/input';
import { useDepartments } from './hooks/useOrg';
import type { OrgDepartment } from './types';

// ── Status → colour ────────────────────────────────────────────────────────

const AGENT_STATUS_COLOR: Record<string, string> = {
  idle:       '#475569',
  planning:   '#818CF8',
  executing:  '#34D399',
  waiting:    '#FBB724',
  blocked:    '#F87171',
  escalated:  '#F97316',
  success:    '#10B981',
  failed:     '#EF4444',
};

// ── Department colours (from spec) ─────────────────────────────────────────

const DEPT_COLORS: Record<string, string> = {
  executive:   '#7C3AED', engineering: '#2563EB', marketing:   '#EC4899',
  finance:     '#16A34A', legal:       '#CA8A04', hr:          '#7C6FAB',
  security:    '#DC2626', data:        '#0891B2', sales:       '#EA580C',
  operations:  '#6B7280', research:    '#9333EA', product:     '#DB2777',
};

function deptColor(name: string): string {
  const key = name.toLowerCase().replace(/[\s/]+/g, '');
  return DEPT_COLORS[key] ?? '#6366F1';
}

// ── Custom nodes ───────────────────────────────────────────────────────────

function DeptNode({ data }: { data: { label: string; color: string; agentCount: number } }) {
  return (
    <div
      className="rounded-xl border px-4 py-2.5 min-w-[140px] shadow-lg"
      style={{ backgroundColor: `${data.color}15`, borderColor: `${data.color}40` }}
    >
      <Handle type="target" position={Position.Top} style={{ background: data.color, border: 'none' }} />
      <div className="flex items-center gap-2">
        <Building2 className="h-3.5 w-3.5 flex-shrink-0" style={{ color: data.color }} aria-hidden />
        <span className="text-[12px] font-semibold text-[#F1F5F9] truncate">{data.label}</span>
      </div>
      <p className="text-[10px] mt-0.5" style={{ color: data.color }}>{data.agentCount} agents</p>
      <Handle type="source" position={Position.Bottom} style={{ background: data.color, border: 'none' }} />
    </div>
  );
}

function AgentNode({ data }: { data: { label: string; status: string; role: string } }) {
  const color = AGENT_STATUS_COLOR[data.status] ?? AGENT_STATUS_COLOR.idle;
  const isPulsing = data.status === 'executing' || data.status === 'escalated';
  return (
    <div className="relative rounded-xl border border-[#1E2535] bg-[#0F1623] px-3 py-2 min-w-[120px]">
      <Handle type="target" position={Position.Top} style={{ background: '#1E2535', border: 'none' }} />
      <div className="flex items-center gap-1.5">
        <div className="relative">
          <div className="h-5 w-5 rounded-full bg-[#1A1F2E] border border-[#1E2535] flex items-center justify-center">
            <User className="h-3 w-3 text-[#475569]" aria-hidden />
          </div>
          <span
            className={cn('absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full border border-[#0F1623]', isPulsing && 'animate-pulse')}
            style={{ backgroundColor: color }}
            aria-label={data.status}
          />
        </div>
        <div className="min-w-0">
          <p className="text-[11px] font-medium text-[#F1F5F9] truncate">{data.label}</p>
          <p className="text-[9px] text-[#475569] truncate">{data.role}</p>
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} style={{ background: '#1E2535', border: 'none' }} />
    </div>
  );
}

const NODE_TYPES = { dept: DeptNode, agent: AgentNode };

// ── Build graph from departments ───────────────────────────────────────────

function buildGraph(
  departments: OrgDepartment[],
  orgId: string,
): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = [];
  const edges: Edge[] = [];

  // Org root node
  nodes.push({
    id: `org-${orgId}`,
    type: 'input',
    position: { x: 400, y: 0 },
    data: { label: 'Organization' },
    style: {
      background: '#00D4FF15', border: '1px solid #00D4FF40',
      borderRadius: 12, padding: '8px 16px',
      color: '#F1F5F9', fontSize: 13, fontWeight: 600,
    },
  });

  departments.forEach((dept, di) => {
    const color = deptColor(dept.name);
    const deptX = (di % 4) * 240 + 80;
    const deptY = Math.floor(di / 4) * 200 + 100;

    // Dept node
    nodes.push({
      id: `dept-${dept.id}`,
      type: 'dept',
      position: { x: deptX, y: deptY },
      data: { label: dept.name, color, agentCount: dept.agent_count ?? 0 },
    });

    edges.push({
      id: `e-org-dept-${dept.id}`,
      source: `org-${orgId}`,
      target: `dept-${dept.id}`,
      animated: false,
      style: { stroke: color, opacity: 0.4, strokeWidth: 1.5 },
    });
  });

  return { nodes, edges };
}

// ── Main ───────────────────────────────────────────────────────────────────

interface OrgMapProps {
  orgId: string;
  className?: string;
}

function OrgMapInner({ orgId, className }: OrgMapProps) {
  const [search, setSearch] = useState('');

  const { data: deptData, isLoading, refetch, isFetching } = useDepartments(orgId);
  const departments: OrgDepartment[] = Array.isArray(deptData) ? deptData : (deptData as any)?.data ?? [];

  const { nodes: initNodes, edges: initEdges } = useMemo(
    () => buildGraph(departments, orgId),
    [departments, orgId],
  );

  const [nodes, setNodes, onNodesChange] = useNodesState(initNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initEdges);

  // Rebuild when departments change
  useEffect(() => {
    const { nodes: n, edges: e } = buildGraph(departments, orgId);
    setNodes(n as any);
    setEdges(e as any);
  }, [departments, orgId, setNodes, setEdges]);

  // Search highlight
  const filteredNodes = useMemo(() => {
    if (!search) return nodes;
    return nodes.map(n => ({
      ...n,
      style: {
        ...n.style,
        opacity: String(n.data.label).toLowerCase().includes(search.toLowerCase()) ? 1 : 0.3,
      },
    }));
  }, [nodes, search]);

  return (
    <JARVISPageShell className={cn('flex flex-col h-full', className)}>
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-[#1E2535] shrink-0">
        <div className="relative flex-1 max-w-xs">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[#475569]" aria-hidden />
          <Input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search nodes…"
            className="pl-8 bg-[#0F1623] border-[#1E2535] text-[#F1F5F9] placeholder:text-[#475569] text-xs h-8"
            aria-label="Search org map"
          />
          {search && (
            <button onClick={() => setSearch('')} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[#475569] hover:text-[#94A3B8]" aria-label="Clear search">
              <X className="h-3 w-3" aria-hidden />
            </button>
          )}
        </div>

        <button
          onClick={() => refetch()}
          disabled={isFetching}
          aria-label="Refresh org map"
          style={{ touchAction: 'manipulation' }}
          className="p-1.5 rounded-lg text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50"
        >
          <RefreshCw className={cn('h-4 w-4', isFetching && 'animate-spin')} aria-hidden />
        </button>

        <div className="flex items-center gap-3 text-[10px] text-[#475569] ml-auto">
          {Object.entries(AGENT_STATUS_COLOR).slice(0, 4).map(([status, color]) => (
            <div key={status} className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} aria-hidden />
              <span className="capitalize">{status}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Graph */}
      <div className="flex-1">
        {isLoading ? (
          <div className="flex items-center justify-center h-full">
            <RefreshCw className="h-5 w-5 animate-spin text-[#00D4FF]" aria-label="Loading org map" />
          </div>
        ) : (
          <ReactFlow
            nodes={filteredNodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            fitView
            fitViewOptions={{ padding: 0.2 }}
            style={{ background: '#0A0D14' }}
            defaultEdgeOptions={{
              markerEnd: { type: MarkerType.ArrowClosed },
            }}
          >
            <Background color="#1E2535" gap={24} size={1} />
            <Controls style={{ background: '#0F1623', border: '1px solid #1E2535', borderRadius: 8 }} />
            <MiniMap
              style={{ background: '#0F1623', border: '1px solid #1E2535', borderRadius: 8 }}
              nodeColor={(node) => {
                if (node.type === 'dept') return (node.data as any).color ?? '#6366F1';
                return AGENT_STATUS_COLOR[(node.data as any).status] ?? '#475569';
              }}
            />
          </ReactFlow>
        )}
      </div>
    </JARVISPageShell>
  );
}

export function OrgMap(props: OrgMapProps) {
  return (
    <ReactFlowProvider>
      <OrgMapInner {...props} />
    </ReactFlowProvider>
  );
}

export default OrgMap;
