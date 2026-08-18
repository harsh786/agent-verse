/**
 * OrgChart — interactive hierarchy graph using React Flow.
 *
 * Features:
 *  - Animated org tree: Org → Departments → Teams → Agents
 *  - Click dept → expand to agents
 *  - Click agent → slide-in AgentProfile panel
 *  - Agent status colour-coding (idle/executing/blocked/escalated)
 *  - Search to highlight nodes
 *  - Zoom: org level → dept → agent
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import ReactFlow, {
  Background,
  Controls,
  Edge,
  Handle,
  MiniMap,
  Node,
  Position,
  useEdgesState,
  useNodesState,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Building2, Users, User, ChevronRight, Search, X,
  Activity, Clock, AlertTriangle, CheckCircle2,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { useOrganization, useOrgDepartments } from './hooks/useOrg';
import type { OrgDepartment } from './types';

// ── Agent status colours ───────────────────────────────────────────────────

const AGENT_STATUS_COLOR: Record<string, string> = {
  idle:       'bg-[var(--text-muted)] text-[var(--bg-base)]',
  planning:   'bg-indigo-400 text-white',
  executing:  'bg-emerald-400 text-white',
  waiting:    'bg-yellow-400 text-black',
  blocked:    'bg-red-400 text-white',
  escalated:  'bg-orange-400 text-white',
  success:    'bg-emerald-500 text-white',
  failed:     'bg-red-500 text-white',
};

const DEPT_COLOR: Record<string, string> = {
  executive:   '#7C3AED',
  engineering: '#2563EB',
  marketing:   '#EC4899',
  finance:     '#16A34A',
  legal:       '#CA8A04',
  hr:          '#7C6FAB',
  security:    '#DC2626',
  data:        '#0891B2',
  sales:       '#EA580C',
  operations:  '#6B7280',
  research:    '#9333EA',
  product:     '#DB2777',
  content:     '#0D9488',
  support:     '#F59E0B',
};

// ── Custom node components ─────────────────────────────────────────────────

function OrgNode({ data }: { data: { label: string; subtitle: string; health: number } }) {
  return (
    <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-xl px-5 py-3 min-w-[180px] shadow-lg">
      <Handle type="source" position={Position.Bottom} />
      <div className="flex items-center gap-2">
        <Building2 className="h-4 w-4 text-[var(--accent-blue)]" />
        <span className="font-semibold text-[var(--text-primary)] text-sm">{data.label}</span>
      </div>
      <div className="text-xs text-[var(--text-muted)] mt-1">{data.subtitle}</div>
      <div className="mt-2 flex items-center gap-1">
        <div className="h-1 flex-1 rounded bg-[var(--bg-surface)]">
          <div
            className="h-1 rounded bg-[var(--accent-blue)]"
            style={{ width: `${data.health}%` }}
          />
        </div>
        <span className="text-[10px] text-[var(--text-muted)]">{data.health}%</span>
      </div>
    </div>
  );
}

function DeptNode({
  data,
}: {
  data: {
    label: string;
    kind: string;
    agentCount: number;
    expanded: boolean;
    onToggle: () => void;
  };
}) {
  const color = DEPT_COLOR[data.kind] ?? '#6B7280';
  return (
    <div
      className="bg-[var(--bg-card)] border border-[var(--border)] rounded-xl px-4 py-2.5 min-w-[160px] cursor-pointer hover:shadow-glow-electric transition-shadow"
      onClick={data.onToggle}
      role="button"
      aria-expanded={data.expanded}
      aria-label={`${data.label} department, ${data.agentCount} agents`}
    >
      <Handle type="target" position={Position.Top} />
      <Handle type="source" position={Position.Bottom} />
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: color }} />
          <span className="font-medium text-[var(--text-primary)] text-sm">{data.label}</span>
        </div>
        <ChevronRight
          className={`h-3 w-3 text-[var(--text-muted)] transition-transform ${data.expanded ? 'rotate-90' : ''}`}
        />
      </div>
      <div className="flex items-center gap-1 mt-1">
        <Users className="h-3 w-3 text-[var(--text-muted)]" />
        <span className="text-[10px] text-[var(--text-muted)]">{data.agentCount} agents</span>
      </div>
    </div>
  );
}

function AgentNode({ data }: { data: { label: string; role: string; status: string } }) {
  const statusClass = AGENT_STATUS_COLOR[data.status] ?? AGENT_STATUS_COLOR.idle;
  const StatusIcon =
    data.status === 'executing' ? Activity
    : data.status === 'blocked' ? AlertTriangle
    : data.status === 'success' ? CheckCircle2
    : Clock;

  return (
    <div className="bg-[var(--bg-surface)] border border-[var(--border)] rounded-lg px-3 py-2 min-w-[140px]">
      <Handle type="target" position={Position.Top} />
      <div className="flex items-center gap-2">
        <div className={`w-5 h-5 rounded-full flex items-center justify-center flex-shrink-0 ${statusClass}`}>
          <User className="h-3 w-3" aria-hidden="true" />
        </div>
        <div className="min-w-0">
          <div className="text-xs font-medium text-[var(--text-primary)] truncate">{data.label}</div>
          <div className="text-[10px] text-[var(--text-muted)] truncate">{data.role}</div>
        </div>
      </div>
      <div className="mt-1.5 flex items-center gap-1">
        <StatusIcon className="h-3 w-3 text-[var(--text-muted)]" aria-hidden="true" />
        <span className="text-[10px] text-[var(--text-muted)] capitalize">{data.status}</span>
      </div>
    </div>
  );
}

const NODE_TYPES = { org: OrgNode, dept: DeptNode, agent: AgentNode };

// ── Main component ─────────────────────────────────────────────────────────

interface OrgChartProps {
  orgId: string;
  onAgentClick?: (agentId: string) => void;
}

export function OrgChart({ orgId, onAgentClick }: OrgChartProps) {
  const { data: org } = useOrganization(orgId);
  const { data: depts } = useOrgDepartments(orgId);
  const [expandedDepts, setExpandedDepts] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState('');
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  const toggleDept = useCallback((deptId: string) => {
    setExpandedDepts(prev => {
      const next = new Set(prev);
      if (next.has(deptId)) next.delete(deptId);
      else next.add(deptId);
      return next;
    });
  }, []);

  // Build graph from org data
  useEffect(() => {
    if (!org || !depts) return;

    const newNodes: Node[] = [];
    const newEdges: Edge[] = [];

    // Org root node
    const orgNodeId = `org-${org.id}`;
    newNodes.push({
      id: orgNodeId,
      type: 'org',
      position: { x: 400, y: 0 },
      data: {
        label: org.name,
        subtitle: `${org.industry || 'AI Organisation'}`,
        health: 87,
      },
    });

    // Department nodes
    const deptList: OrgDepartment[] = Array.isArray(depts) ? depts : (depts as any)?.data ?? [];
    const deptSpacing = Math.max(200, 900 / Math.max(deptList.length, 1));

    deptList.forEach((dept, idx) => {
      const deptNodeId = `dept-${dept.id}`;
      const x = idx * deptSpacing - ((deptList.length - 1) * deptSpacing) / 2 + 400;

      newNodes.push({
        id: deptNodeId,
        type: 'dept',
        position: { x, y: 140 },
        data: {
          label: dept.name,
          kind: dept.capability_domains?.[0] ?? 'operations',
          agentCount: (dept as any).agent_count ?? 0,
          expanded: expandedDepts.has(dept.id),
          onToggle: () => toggleDept(dept.id),
        },
      });

      newEdges.push({
        id: `e-org-dept-${dept.id}`,
        source: orgNodeId,
        target: deptNodeId,
        style: { stroke: 'var(--border)', strokeWidth: 1.5 },
        animated: false,
      });

      // Agent placeholder nodes when dept is expanded
      if (expandedDepts.has(dept.id)) {
        const mockAgents = [
          { id: `${dept.id}-a1`, name: 'Agent Alpha', role: 'Analyst', status: 'executing' },
          { id: `${dept.id}-a2`, name: 'Agent Beta', role: 'Researcher', status: 'idle' },
        ];
        mockAgents.forEach((agent, aIdx) => {
          const agentNodeId = `agent-${agent.id}`;
          newNodes.push({
            id: agentNodeId,
            type: 'agent',
            position: { x: x + (aIdx - 0.5) * 160, y: 280 },
            data: { label: agent.name, role: agent.role, status: agent.status },
          });
          newEdges.push({
            id: `e-dept-agent-${agent.id}`,
            source: deptNodeId,
            target: agentNodeId,
            style: { stroke: 'var(--border)', strokeWidth: 1 },
          });
        });
      }
    });

    setNodes(newNodes);
    setEdges(newEdges);
  }, [org, depts, expandedDepts, toggleDept, setNodes, setEdges]);

  // Filter by search
  const filteredNodes = useMemo(() => {
    if (!search) return nodes;
    const q = search.toLowerCase();
    return nodes.map(n => ({
      ...n,
      style: {
        ...n.style,
        opacity: (n.data?.label as string ?? '').toLowerCase().includes(q) ? 1 : 0.3,
      },
    }));
  }, [nodes, search]);

  return (
    <div className="relative w-full h-full bg-[var(--bg-base)] rounded-xl overflow-hidden">
      {/* Search */}
      <div className="absolute top-4 left-4 z-10 w-56">
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-[var(--text-muted)]" aria-hidden="true" />
          <Input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search org…"
            className="pl-8 h-8 text-xs bg-[var(--bg-card)] border-[var(--border)]"
            aria-label="Search organisation chart"
          />
          {search && (
            <button
              onClick={() => setSearch('')}
              className="absolute right-2 top-2 text-[var(--text-muted)] hover:text-[var(--text-primary)]"
              aria-label="Clear search"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* Legend */}
      <div className="absolute top-4 right-4 z-10 flex gap-3">
        {(['executing', 'idle', 'blocked'] as const).map(s => (
          <div key={s} className="flex items-center gap-1">
            <div className={`w-2 h-2 rounded-full ${AGENT_STATUS_COLOR[s]}`} aria-hidden="true" />
            <span className="text-[10px] text-[var(--text-muted)] capitalize">{s}</span>
          </div>
        ))}
      </div>

      <ReactFlow
        nodes={filteredNodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        proOptions={{ hideAttribution: true }}
        onNodeClick={(_, node) => {
          if (node.type === 'agent' && onAgentClick) {
            onAgentClick(node.id.replace('agent-', ''));
          }
        }}
        aria-label="Organisation chart"
      >
        <Background color="var(--border)" gap={24} size={1} />
        <Controls aria-label="Chart controls" />
        <MiniMap
          nodeColor={n => {
            if (n.type === 'org') return 'var(--accent-blue)';
            if (n.type === 'dept') return DEPT_COLOR[(n.data?.kind as string) ?? ''] ?? '#6B7280';
            return 'var(--text-muted)';
          }}
          style={{ background: 'var(--bg-surface)' }}
        />
      </ReactFlow>
    </div>
  );
}
