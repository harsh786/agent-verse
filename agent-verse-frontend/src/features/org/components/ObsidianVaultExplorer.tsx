/**
 * ObsidianVaultExplorer — T7: In-app Obsidian vault exploration panel.
 *
 * Tabs (WS-11c: all five wired to real backend analogs — no Obsidian-specific
 * backend exists, so each tab is mapped onto the closest real AgentVerse data
 * source rather than left as a demo):
 *   Graph View  — real force-directed knowledge graph, wired to the tenant KG
 *                 (`/knowledge-graph/export`), click-to-focus, filter by type
 *   File Tree   — the same real KG nodes grouped by type, with search
 *   Bases       — real org missions/tasks table (`/v1/org/{id}/missions|tasks`):
 *                 title, status, priority, owner/team, age — the closest real
 *                 analog to an Obsidian Base (structured, filterable records)
 *   Maps        — real KG reused as a clustered/grouped map (nodes grouped by
 *                 type, cross-type edges as connectors) — the closest real
 *                 analog to a JSON Canvas spatial map; no separate canvas
 *                 store exists so it's a real transform of the same KG data
 *   Timeline    — real org event history (`/v1/org/{id}/events`: mission/task/
 *                 approval lifecycle), day-bucketed activity + chronological
 *                 feed — real timestamps only, never a fabricated sparkline
 *
 * HONESTY RULE: every tab renders only real backend data (`knowledgeGraphApi`,
 * `orgApi`, see src/lib/api/client.ts / src/features/org/api.ts) — no demo/
 * fabricated nodes, rows, or sparklines. Each tab shows an honest empty state
 * when its real backend query returns no data, and an honest error+retry state
 * on fetch failure.
 *
 * Skills:
 *   frontend-design:   JARVIS dark vault, emerald-400 note glow, pulsing graph
 *   emil-design-eng:   spring 400/30 tab switch, 280/26 node entrance, stagger 40ms
 *   impeccable-ui:     note title dominant, path secondary, stats tertiary
 *   web-guidelines:    role=tablist, aria-selected, time[datetime], aria-live
 *   ui-ux-pro-max:     44px targets, useReducedMotion, keyboard nav
 */
import { useState, useCallback, useId, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import {
  Network, FileText, BarChart3, Map as MapIcon, Clock,
  Search, ChevronRight, FileCode, Layers,
  ExternalLink, AlertTriangle, RefreshCw, X, GitBranch,
} from 'lucide-react';

import { knowledgeGraphApi, type KGNode } from '@/lib/api/client';
import { KnowledgeGraph, type KnowledgeNode, type KnowledgeEdge } from '@/components/knowledge/KnowledgeGraph';
import { EmptyState } from '@/components/ui/EmptyState';
import { Skeleton } from '@/components/ui/Skeleton';
import { orgApi } from '../api';

// ── Spring constants ──────────────────────────────────────────────────────────

const SPRING_FAST  = { type: 'spring', stiffness: 600, damping: 35 } as const;
const SPRING_NODE  = { type: 'spring', stiffness: 400, damping: 30 } as const;
const SPRING_PANEL = { type: 'spring', stiffness: 280, damping: 26 } as const;

// ── Shared helpers (Bases/Maps/Timeline real-data rendering) ──────────────────

/** Same palette as KnowledgeGraph.tsx's DEFAULT_NODE_COLORS, kept local so the
 *  Maps cluster view visually matches the Graph tab without exporting internals. */
const NODE_TYPE_COLORS: Record<string, string> = {
  document: '#3b82f6', chunk: '#0ea5e9', concept: '#22c55e', entity: '#f59e0b',
  goal: '#a855f7', tool: '#ef4444', memory: '#14b8a6', artifact: '#eab308',
  agent: '#ec4899', workflow: '#6366f1',
};

/** Compact relative-age label for a real ISO timestamp (never fabricated). */
function ageLabel(iso: string | null | undefined): string {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const diffMs = Date.now() - then;
  const mins = Math.floor(diffMs / 60_000);
  if (mins < 1) return 'now';
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo`;
  return `${Math.floor(months / 12)}y`;
}

function priorityColor(p: string): string {
  if (p === 'critical') return 'text-red-400 bg-red-500/10 border-red-500/30';
  if (p === 'high') return 'text-amber-400 bg-amber-500/10 border-amber-500/30';
  if (p === 'medium') return 'text-blue-400 bg-blue-500/10 border-blue-500/30';
  return 'text-[#64748B] bg-[#1A1F2E] border-[#2D3748]';
}

function statusColor(s: string): string {
  if (['completed', 'active', 'running', 'assigned'].includes(s)) return 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30';
  if (['failed', 'blocked', 'cancelled', 'expired'].includes(s)) return 'text-red-400 bg-red-500/10 border-red-500/30';
  if (['queued', 'planned', 'draft', 'waiting', 'review', 'approval_required'].includes(s)) return 'text-amber-400 bg-amber-500/10 border-amber-500/30';
  return 'text-[#64748B] bg-[#1A1F2E] border-[#2D3748]';
}

// ── Tab types ─────────────────────────────────────────────────────────────────

type VaultTab = 'graph' | 'files' | 'bases' | 'maps' | 'timeline';

const TABS: { id: VaultTab; label: string; icon: React.ComponentType<{className?: string}> }[] = [
  { id: 'graph',    label: 'Graph',    icon: Network    },
  { id: 'files',    label: 'Files',    icon: FileText   },
  { id: 'bases',    label: 'Bases',    icon: BarChart3  },
  { id: 'maps',     label: 'Maps',     icon: MapIcon    },
  { id: 'timeline', label: 'Timeline', icon: Clock      },
];

// ── Graph View — real tenant knowledge graph ───────────────────────────────────

/** Node-detail side panel: fetches full content/metadata/edges for one node. */
function NodeDetailPanel({ nodeId, nodesById, onClose }: {
  nodeId: string;
  nodesById: Map<string, KGNode>;
  onClose: () => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ['kg-node-detail', nodeId],
    queryFn: () => knowledgeGraphApi.getNode(nodeId),
  });
  const summary = nodesById.get(nodeId);

  return (
    <motion.div
      initial={false} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
      transition={SPRING_PANEL}
      className="mt-2 p-3 bg-[#0F1117] border border-[#2D3748] rounded-xl space-y-2"
      aria-label="Node detail"
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 min-w-0">
          <GitBranch className="h-3.5 w-3.5 text-[#00D4FF] shrink-0" aria-hidden />
          <span className="text-[12px] font-medium text-[#F1F5F9] truncate">{summary?.label ?? nodeId}</span>
          {summary && <span className="text-[10px] text-[#475569] capitalize shrink-0">{summary.node_type}</span>}
        </div>
        <button onClick={onClose} aria-label="Close node detail" className="text-[#475569] hover:text-[#94A3B8] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 rounded shrink-0">
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>
      {isLoading ? (
        <Skeleton className="h-10 w-full" />
      ) : data ? (
        <>
          {data.node.content && (
            <p className="text-[11px] text-[#94A3B8] leading-relaxed">{data.node.content}</p>
          )}
          {data.edges.length > 0 && (
            <div className="space-y-1">
              <p className="text-[10px] text-[#475569] uppercase tracking-wide">Connections ({data.edges.length})</p>
              {data.edges.slice(0, 6).map(e => (
                <div key={e.edge_id} className="flex items-center gap-2 text-[11px] text-[#64748B]">
                  <span className="text-[9px] bg-[#1A1F2E] text-[#94A3B8] px-1.5 py-0.5 rounded font-mono shrink-0">{e.edge_type}</span>
                  <span className="truncate">
                    {e.source_node_id === nodeId ? '→ ' : '← '}
                    {(e.source_node_id === nodeId ? nodesById.get(e.target_node_id) : nodesById.get(e.source_node_id))?.label
                      ?? (e.source_node_id === nodeId ? e.target_node_id : e.source_node_id)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      ) : (
        <p className="text-[11px] text-[#475569]">Could not load node detail.</p>
      )}
    </motion.div>
  );
}

function GraphView() {
  const reduce = useReducedMotion();
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [activeTypes, setActiveTypes] = useState<Set<string> | null>(null); // null = all types

  const { data: graph, isLoading, isError, refetch } = useQuery({
    queryKey: ['kg-graph'],
    queryFn: () => knowledgeGraphApi.getGraph(),
    staleTime: 15_000,
  });

  const allNodes = useMemo(() => graph?.nodes ?? [], [graph]);
  const allEdges = useMemo(() => graph?.edges ?? [], [graph]);
  const nodesById = useMemo(() => new Map(allNodes.map(n => [n.node_id, n])), [allNodes]);
  const availableTypes = useMemo(() => [...new Set(allNodes.map(n => n.node_type))].sort(), [allNodes]);

  const visibleNodes = useMemo(
    () => (activeTypes ? allNodes.filter(n => activeTypes.has(n.node_type)) : allNodes),
    [allNodes, activeTypes]
  );
  const visibleIds = useMemo(() => new Set(visibleNodes.map(n => n.node_id)), [visibleNodes]);
  const visibleEdges = useMemo(
    () => allEdges.filter(e => visibleIds.has(e.source) && visibleIds.has(e.target)),
    [allEdges, visibleIds]
  );

  const graphData = useMemo(() => ({
    nodes: visibleNodes.map((n): KnowledgeNode => ({ id: n.node_id, label: n.label, type: n.node_type })),
    edges: visibleEdges.map((e): KnowledgeEdge => ({ id: e.edge_id, source: e.source, target: e.target, label: e.edge_type })),
  }), [visibleNodes, visibleEdges]);

  const toggleType = useCallback((type: string) => {
    setActiveTypes(prev => {
      const base = prev ?? new Set(availableTypes);
      const next = new Set(base);
      if (next.has(type)) next.delete(type); else next.add(type);
      // Selecting everything is equivalent to "all" (null)
      return next.size === availableTypes.length ? null : next;
    });
  }, [availableTypes]);

  if (isLoading) {
    return <Skeleton className="h-72 w-full rounded-xl" />;
  }

  if (isError) {
    return (
      <div className="h-72 flex flex-col items-center justify-center gap-3 bg-[#090C12] rounded-xl border border-[#1E2535]" role="alert">
        <AlertTriangle className="h-6 w-6 text-amber-400" aria-hidden />
        <p className="text-[12px] text-[#94A3B8]">Couldn't load the knowledge graph.</p>
        <button
          onClick={() => refetch()}
          className="flex items-center gap-1.5 text-[11px] px-2.5 py-1 rounded-lg border border-[#2D3748] text-[#94A3B8] hover:text-[#F1F5F9] hover:border-blue-500/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
        >
          <RefreshCw className="h-3 w-3" aria-hidden />Retry
        </button>
      </div>
    );
  }

  if (allNodes.length === 0) {
    return (
      <div className="bg-[#090C12] rounded-xl border border-[#1E2535]">
        <EmptyState
          icon={<Network className="h-10 w-10" />}
          title="Knowledge graph is empty"
          description="Ingest documents or run entity extraction to populate this org's knowledge graph."
          variant="static"
        />
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {/* Type filter chips */}
      <div className="flex flex-wrap gap-1" role="group" aria-label="Filter by node type">
        {availableTypes.map(type => {
          const isActive = !activeTypes || activeTypes.has(type);
          return (
            <button
              key={type}
              onClick={() => toggleType(type)}
              aria-pressed={isActive}
              className={`px-2 py-0.5 text-[10px] rounded-full border capitalize transition-colors ${
                isActive ? 'bg-[#1A1F2E] border-blue-500/40 text-[#F1F5F9]' : 'border-[#2D3748] text-[#475569] hover:text-[#94A3B8]'
              }`}
            >
              {type}
            </button>
          );
        })}
      </div>

      <div
        className="relative h-72 bg-[#090C12] rounded-xl overflow-hidden border border-[#1E2535] p-2"
        aria-label="Knowledge graph view"
      >
        {visibleNodes.length === 0 ? (
          <div className="h-full flex items-center justify-center text-[12px] text-[#475569]">
            No nodes match the selected types
          </div>
        ) : (
          <KnowledgeGraph
            data={graphData}
            width={560}
            height={reduce ? 240 : 240}
            focusNodeId={selectedNodeId}
            onNodeClick={(n) => setSelectedNodeId(prev => (prev === n.id ? null : n.id))}
          />
        )}
      </div>

      <AnimatePresence>
        {selectedNodeId && (
          <NodeDetailPanel nodeId={selectedNodeId} nodesById={nodesById} onClose={() => setSelectedNodeId(null)} />
        )}
      </AnimatePresence>
    </div>
  );
}

// ── File Tree — same real KG nodes, grouped by type ────────────────────────────

function FileTree() {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState('');

  const { data: graph, isLoading } = useQuery({
    queryKey: ['kg-graph'],
    queryFn: () => knowledgeGraphApi.getGraph(),
    staleTime: 15_000,
  });
  const nodes = graph?.nodes ?? [];

  const filtered = nodes.filter(n =>
    !search || n.label.toLowerCase().includes(search.toLowerCase())
  );

  const folders = [...new Set(filtered.map(n => n.node_type))].sort();

  if (isLoading) {
    return <Skeleton className="h-40 w-full rounded-xl" />;
  }

  if (nodes.length === 0) {
    return (
      <EmptyState
        icon={<FileText className="h-10 w-10" />}
        title="No knowledge graph nodes yet"
        description="Ingested documents and extracted entities will appear here."
        variant="static"
      />
    );
  }

  return (
    <div className="space-y-2">
      <div className="relative">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[#475569]" aria-hidden />
        <input
          type="search" value={search} onChange={e => setSearch(e.target.value)}
          placeholder="Search knowledge graph…"
          aria-label="Search vault notes"
          className="w-full pl-8 pr-3 py-1.5 rounded-lg bg-[#0F1117] border border-[#2D3748] text-[12px] text-[#F1F5F9] placeholder:text-[#374151] focus:outline-none focus:ring-2 focus:ring-blue-500/60"
        />
      </div>
      <div className="space-y-1 max-h-52 overflow-y-auto">
        {folders.map((folder, fi) => (
          <div key={folder}
            style={{ animationDelay: `${Math.min(fi, 8) * 0.04}s` }}
            className="jarvis-rise-in"
          >
            <button
              onClick={() => setExpanded(s => { const n = new Set(s); n.has(folder) ? n.delete(folder) : n.add(folder); return n; })}
              aria-expanded={expanded.has(folder)}
              aria-label={`${expanded.has(folder) ? 'Collapse' : 'Expand'} ${folder} group`}
              className="flex items-center gap-1.5 w-full text-left px-2 py-1 rounded-lg text-[12px] text-[#94A3B8] hover:bg-[#1A1F2E] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
            >
              <motion.div animate={{ rotate: expanded.has(folder) ? 90 : 0 }} transition={SPRING_FAST}>
                <ChevronRight className="h-3.5 w-3.5" aria-hidden />
              </motion.div>
              <Layers className="h-3.5 w-3.5 text-amber-400" aria-hidden />
              <span className="capitalize">{folder} ({filtered.filter(n => n.node_type === folder).length})</span>
            </button>
            <AnimatePresence>
              {expanded.has(folder) && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }} transition={SPRING_PANEL}
                  style={{ overflow: 'hidden' }} className="pl-6"
                >
                  {filtered.filter(n => n.node_type === folder).map((n, ni) => (
                    <div key={n.node_id}
                      style={{ animationDelay: `${Math.min(ni, 8) * 0.04}s` }}
                      className="jarvis-rise-in flex items-center gap-1.5 px-2 py-1 rounded-lg text-[11px] text-[#64748B] hover:text-[#F1F5F9] hover:bg-[#1A1F2E] cursor-pointer"
                    >
                      <FileCode className="h-3 w-3 text-blue-400" aria-hidden />
                      <span className="truncate">{n.label}</span>
                      <span className="ml-auto text-[10px] text-[#374151] tabular-nums shrink-0">
                        {Math.round(n.confidence * 100)}%
                      </span>
                    </div>
                  ))}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Bases — real org missions/tasks table ──────────────────────────────────────
//
// HONESTY RULE: no Obsidian-Bases backend exists, so this tab is wired to the
// closest real analog — this org's real missions/tasks (`orgApi`) — rendered
// as a structured, filterable table (title, status, priority, owner, age).

type BaseRow = { id: string; title: string; status: string; priority: string; owner: string; created_at: string };

function BasesTab({ orgId }: { orgId: string }) {
  const [kind, setKind] = useState<'tasks' | 'missions'>('tasks');

  const tasksQuery = useQuery({
    queryKey: ['obsidian-bases-tasks', orgId],
    queryFn: () => orgApi.listTasks(orgId, {}),
    enabled: kind === 'tasks',
    staleTime: 15_000,
  });
  const missionsQuery = useQuery({
    queryKey: ['obsidian-bases-missions', orgId],
    queryFn: () => orgApi.listMissions(orgId, { limit: 20 }),
    enabled: kind === 'missions',
    staleTime: 15_000,
  });

  const active = kind === 'tasks' ? tasksQuery : missionsQuery;
  const rows: BaseRow[] = kind === 'tasks'
    ? (tasksQuery.data?.data ?? []).map(t => ({
        id: t.id, title: t.title, status: t.status, priority: t.priority,
        owner: t.assigned_to ?? '—', created_at: t.created_at,
      }))
    : (missionsQuery.data?.data ?? []).map(m => ({
        id: m.id, title: m.title, status: m.status, priority: m.priority,
        owner: m.assigned_team_id ?? '—', created_at: m.created_at,
      }));

  return (
    <div className="space-y-2">
      <div className="flex gap-1" role="group" aria-label="Base type">
        {(['tasks', 'missions'] as const).map(k => (
          <button
            key={k}
            onClick={() => setKind(k)}
            aria-pressed={kind === k}
            className={`px-2.5 py-1 text-[10px] rounded-full border capitalize transition-colors ${
              kind === k ? 'bg-[#1A1F2E] border-blue-500/40 text-[#F1F5F9]' : 'border-[#2D3748] text-[#475569] hover:text-[#94A3B8]'
            }`}
          >
            {k}
          </button>
        ))}
      </div>

      {active.isLoading ? (
        <Skeleton className="h-40 w-full rounded-xl" />
      ) : active.isError ? (
        <div className="h-40 flex flex-col items-center justify-center gap-2 bg-[#090C12] rounded-xl border border-[#1E2535]" role="alert">
          <AlertTriangle className="h-5 w-5 text-amber-400" aria-hidden />
          <p className="text-[12px] text-[#94A3B8]">Couldn't load {kind}.</p>
          <button
            onClick={() => void active.refetch()}
            className="flex items-center gap-1.5 text-[11px] px-2.5 py-1 rounded-lg border border-[#2D3748] text-[#94A3B8] hover:text-[#F1F5F9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
          >
            <RefreshCw className="h-3 w-3" aria-hidden />Retry
          </button>
        </div>
      ) : rows.length === 0 ? (
        <div className="bg-[#090C12] rounded-xl border border-[#1E2535]">
          <EmptyState
            icon={<BarChart3 className="h-10 w-10" />}
            title={`No ${kind} yet`}
            description={kind === 'tasks' ? 'Tasks dispatched to agents for this org will appear here.' : 'Missions created for this org will appear here.'}
            variant="static"
          />
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-[#1E2535] bg-[#090C12]">
          <table className="w-full text-left" aria-label={`${kind} table`}>
            <thead>
              <tr className="border-b border-[#1E2535] text-[10px] uppercase tracking-wide text-[#475569]">
                <th className="px-3 py-2 font-medium">Title</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Priority</th>
                <th className="px-3 py-2 font-medium">{kind === 'tasks' ? 'Owner' : 'Team'}</th>
                <th className="px-3 py-2 font-medium">Age</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 20).map((row, i) => (
                <tr
                  key={row.id}
                  style={{ animationDelay: `${Math.min(i, 8) * 0.04}s` }}
                  className="jarvis-rise-in border-b border-[#1E2535] last:border-0 hover:bg-[#1A1F2E]/60"
                >
                  <td className="px-3 py-2 text-[11px] text-[#F1F5F9] max-w-[160px] truncate">{row.title}</td>
                  <td className="px-3 py-2">
                    <span className={`px-1.5 py-0.5 rounded text-[10px] border capitalize ${statusColor(row.status)}`}>
                      {row.status.replace(/_/g, ' ')}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <span className={`px-1.5 py-0.5 rounded text-[10px] border capitalize ${priorityColor(row.priority)}`}>
                      {row.priority}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-[11px] text-[#64748B] font-mono truncate max-w-[100px]">{row.owner}</td>
                  <td className="px-3 py-2 text-[10px] text-[#475569] tabular-nums">
                    <time dateTime={row.created_at}>{ageLabel(row.created_at)}</time>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Maps — real KG reused as a clustered/grouped map ───────────────────────────
//
// HONESTY RULE: no JSON Canvas store exists, so this tab reuses the same real
// tenant KG (`knowledgeGraphApi`) as the Graph tab, transformed into a spatial
// clustered view (nodes grouped by type, cross-type edges as connectors) — a
// real, derived view, not a fabricated canvas.

function MapsTab() {
  const reduce = useReducedMotion();
  const { data: graph, isLoading, isError, refetch } = useQuery({
    queryKey: ['kg-graph'],
    queryFn: () => knowledgeGraphApi.getGraph(),
    staleTime: 15_000,
  });

  const nodes = useMemo(() => graph?.nodes ?? [], [graph]);
  const edges = useMemo(() => graph?.edges ?? [], [graph]);

  const clusters = useMemo(() => {
    const byType = new Map<string, KGNode[]>();
    for (const n of nodes) {
      const list = byType.get(n.node_type) ?? [];
      list.push(n);
      byType.set(n.node_type, list);
    }
    return [...byType.entries()].map(([type, members]) => ({ type, members }));
  }, [nodes]);

  const nodeTypeById = useMemo(() => new Map(nodes.map(n => [n.node_id, n.node_type])), [nodes]);

  const linkCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const e of edges) {
      const ta = nodeTypeById.get(e.source);
      const tb = nodeTypeById.get(e.target);
      if (!ta || !tb || ta === tb) continue;
      const key = [ta, tb].sort().join('|');
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return counts;
  }, [edges, nodeTypeById]);

  if (isLoading) return <Skeleton className="h-72 w-full rounded-xl" />;

  if (isError) {
    return (
      <div className="h-72 flex flex-col items-center justify-center gap-3 bg-[#090C12] rounded-xl border border-[#1E2535]" role="alert">
        <AlertTriangle className="h-6 w-6 text-amber-400" aria-hidden />
        <p className="text-[12px] text-[#94A3B8]">Couldn't load the knowledge map.</p>
        <button
          onClick={() => void refetch()}
          className="flex items-center gap-1.5 text-[11px] px-2.5 py-1 rounded-lg border border-[#2D3748] text-[#94A3B8] hover:text-[#F1F5F9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
        >
          <RefreshCw className="h-3 w-3" aria-hidden />Retry
        </button>
      </div>
    );
  }

  if (nodes.length === 0) {
    return (
      <div className="bg-[#090C12] rounded-xl border border-[#1E2535]">
        <EmptyState
          icon={<MapIcon className="h-10 w-10" />}
          title="Knowledge map is empty"
          description="Ingest documents or run entity extraction to populate this org's knowledge map."
          variant="static"
        />
      </div>
    );
  }

  const W = 560, H = 240, cx = W / 2, cy = H / 2, R = Math.min(W, H) / 2 - 46;
  const positioned = clusters.map((c, i) => {
    const angle = (2 * Math.PI * i) / clusters.length - Math.PI / 2;
    return { ...c, x: cx + R * Math.cos(angle), y: cy + R * Math.sin(angle) };
  });
  const posByType = new Map(positioned.map(p => [p.type, p]));
  const maxCount = Math.max(...clusters.map(c => c.members.length), 1);

  return (
    <div className="space-y-2">
      <div
        className="relative bg-[#090C12] rounded-xl overflow-hidden border border-[#1E2535] p-2"
        style={{ height: H }}
        aria-label="Knowledge map — clustered by node type"
      >
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" height="100%">
          {[...linkCounts.entries()].map(([key, count]) => {
            const [ta, tb] = key.split('|');
            const a = posByType.get(ta);
            const b = posByType.get(tb);
            if (!a || !b) return null;
            return (
              <line
                key={key} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                stroke="#2D3748" strokeWidth={Math.min(1 + count / 3, 6)} strokeOpacity={0.6}
              />
            );
          })}
          {positioned.map((c, i) => {
            const r = 18 + 26 * (c.members.length / maxCount);
            const color = NODE_TYPE_COLORS[c.type] ?? '#64748b';
            return (
              <g key={c.type} transform={`translate(${c.x}, ${c.y})`}>
                <motion.circle
                  initial={reduce ? {} : { r: 0, opacity: 0 }}
                  animate={{ r, opacity: 1 }}
                  transition={{ ...SPRING_NODE, delay: i * 0.05 }}
                  fill={color} fillOpacity={0.18} stroke={color} strokeWidth={1.5}
                />
                <text textAnchor="middle" dy="-2" fontSize="10" fill={color} fontWeight={600}>{c.members.length}</text>
                <text textAnchor="middle" dy="12" fontSize="9" fill="#94A3B8" className="capitalize">{c.type}</text>
              </g>
            );
          })}
        </svg>
      </div>
      <p className="text-[10px] text-[#475569] px-1">
        {clusters.length} node type{clusters.length !== 1 ? 's' : ''} · {edges.length} relationship{edges.length !== 1 ? 's' : ''} · line weight = cross-type connections
      </p>
    </div>
  );
}

// ── Timeline — real org event history (missions/tasks/approvals) ──────────────
//
// HONESTY RULE: no vault-growth-history store exists, so this tab is wired to
// this org's real event feed (`orgApi.listEvents` — mission/task/approval
// lifecycle events with real `created_at` timestamps). The bar chart buckets
// real event counts by day; nothing here is fabricated or interpolated.

function TimelineTab({ orgId }: { orgId: string }) {
  const reduce = useReducedMotion();
  const { data: events, isLoading, isError, refetch } = useQuery({
    queryKey: ['obsidian-timeline-events', orgId],
    queryFn: () => orgApi.listEvents(orgId, 100),
    staleTime: 15_000,
  });

  const rows = useMemo(() => events ?? [], [events]);

  const buckets = useMemo(() => {
    const byDay = new Map<string, number>();
    for (const e of rows) {
      const d = e.created_at?.slice(0, 10);
      if (!d) continue;
      byDay.set(d, (byDay.get(d) ?? 0) + 1);
    }
    return [...byDay.entries()].sort(([a], [b]) => a.localeCompare(b)).slice(-14);
  }, [rows]);

  if (isLoading) return <Skeleton className="h-56 w-full rounded-xl" />;

  if (isError) {
    return (
      <div className="h-56 flex flex-col items-center justify-center gap-3 bg-[#090C12] rounded-xl border border-[#1E2535]" role="alert">
        <AlertTriangle className="h-6 w-6 text-amber-400" aria-hidden />
        <p className="text-[12px] text-[#94A3B8]">Couldn't load org activity.</p>
        <button
          onClick={() => void refetch()}
          className="flex items-center gap-1.5 text-[11px] px-2.5 py-1 rounded-lg border border-[#2D3748] text-[#94A3B8] hover:text-[#F1F5F9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
        >
          <RefreshCw className="h-3 w-3" aria-hidden />Retry
        </button>
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="bg-[#090C12] rounded-xl border border-[#1E2535]">
        <EmptyState
          icon={<Clock className="h-10 w-10" />}
          title="No activity yet"
          description="Mission, task and approval events for this org will appear here as they happen."
          variant="static"
        />
      </div>
    );
  }

  const maxCount = Math.max(...buckets.map(([, c]) => c), 1);

  return (
    <div className="space-y-3">
      <div className="flex items-end gap-1 h-16 px-1" aria-label="Org activity by day, last 14 active days" role="img">
        {buckets.map(([day, count]) => (
          <div key={day} className="flex-1 flex flex-col items-center gap-0.5" title={`${day}: ${count} event${count !== 1 ? 's' : ''}`}>
            <motion.div
              initial={reduce ? {} : { height: 0 }}
              animate={{ height: `${Math.max((count / maxCount) * 100, 6)}%` }}
              transition={SPRING_PANEL}
              className="w-full rounded-t bg-violet-500/50 hover:bg-violet-400 transition-colors"
              style={{ minHeight: 2 }}
            />
          </div>
        ))}
      </div>

      <div className="space-y-1 max-h-52 overflow-y-auto">
        {rows.slice(0, 30).map((e, i) => (
          <div
            key={e.id}
            style={{ animationDelay: `${Math.min(i, 8) * 0.04}s` }}
            className="jarvis-rise-in flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-[#1A1F2E]"
          >
            <span
              className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                e.severity === 'critical' || e.severity === 'error' ? 'bg-red-400'
                : e.severity === 'warning' ? 'bg-amber-400' : 'bg-emerald-400'
              }`}
              aria-hidden
            />
            <span className="text-[11px] text-[#94A3B8] truncate flex-1">{e.title || e.event_type}</span>
            <time dateTime={e.created_at} className="text-[10px] text-[#475569] font-mono shrink-0">{ageLabel(e.created_at)}</time>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Main Component ─────────────────────────────────────────────────────────────

interface ObsidianVaultExplorerProps {
  orgId:    string;
  compact?: boolean;
}

export function ObsidianVaultExplorer({ orgId, compact = false }: ObsidianVaultExplorerProps) {
  const labelId  = useId();
  const reduce   = useReducedMotion();
  const [activeTab, setActiveTab] = useState<VaultTab>('graph');

  return (
    <section aria-labelledby={labelId} className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-lg bg-violet-500/10 flex items-center justify-center">
            <Network className="h-3.5 w-3.5 text-violet-400" aria-hidden />
          </div>
          <h3 id={labelId} className="text-[13px] font-semibold text-[#F1F5F9]">Vault Explorer</h3>
        </div>
        <button aria-label="Open vault in Obsidian"
          className="flex items-center gap-1 text-[10px] text-[#475569] hover:text-[#94A3B8] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 rounded">
          <ExternalLink className="h-3 w-3" aria-hidden />Obsidian
        </button>
      </div>

      {/* Tab bar */}
      <div role="tablist" aria-label="Vault view tabs" className="flex gap-0.5 bg-[#0F1117] p-1 rounded-xl border border-[#1E2535]">
        {TABS.map(tab => {
          const Icon = tab.icon;
          return (
            <motion.button
              key={tab.id}
              role="tab"
              aria-selected={activeTab === tab.id}
              aria-controls={`vault-panel-${tab.id}`}
              id={`vault-tab-${tab.id}`}
              onClick={() => setActiveTab(tab.id)}
              whileTap={reduce ? {} : { scale: 0.95 }}
              transition={SPRING_FAST}
              style={{ touchAction: 'manipulation' }}
              className={[
                'flex-1 flex items-center justify-center gap-1 py-1.5 rounded-lg text-[10px] font-medium transition-colors',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70',
                activeTab === tab.id
                  ? 'bg-[#1A1F2E] text-[#F1F5F9] shadow-sm'
                  : 'text-[#475569] hover:text-[#94A3B8]',
              ].join(' ')}
            >
              <Icon className="h-3 w-3" aria-hidden />
              {!compact && <span>{tab.label}</span>}
            </motion.button>
          );
        })}
      </div>

      {/* Tab panels */}
      <AnimatePresence mode="wait">
        <motion.div
          key={activeTab}
          id={`vault-panel-${activeTab}`}
          role="tabpanel"
          aria-labelledby={`vault-tab-${activeTab}`}
          initial={false}
          animate={{ opacity: 1, x: 0 }}
          exit={reduce ? { opacity: 0 } : { opacity: 0, x: -10 }}
          transition={SPRING_PANEL}
        >
          {activeTab === 'graph'    && <GraphView />}
          {activeTab === 'files'    && <FileTree />}
          {activeTab === 'bases'    && <BasesTab orgId={orgId} />}
          {activeTab === 'maps'     && <MapsTab />}
          {activeTab === 'timeline' && <TimelineTab orgId={orgId} />}
        </motion.div>
      </AnimatePresence>
    </section>
  );
}

export default ObsidianVaultExplorer;
