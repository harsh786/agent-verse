/**
 * ObsidianVaultExplorer — T7: In-app Obsidian vault exploration panel.
 *
 * Tabs:
 *   Graph View  — real force-directed knowledge graph, wired to the tenant KG
 *                 (`/knowledge-graph/export`), click-to-focus, filter by type
 *   File Tree   — the same real KG nodes grouped by type, with search
 *   Bases       — preview only: no Obsidian-Bases backend store exists yet
 *   Maps        — preview only: no JSON Canvas backend store exists yet
 *   Timeline    — preview only: no vault-history backend store exists yet
 *
 * HONESTY RULE: the Graph and File Tree tabs render only real backend data
 * (`knowledgeGraphApi`, see src/lib/api/client.ts) — no demo/fabricated nodes.
 * When the tenant's knowledge graph is empty, an honest empty state is shown.
 * Bases/Maps/Timeline have **no backend source** (no `/obsidian/*` API, no
 * vault-store endpoints) — they render an honest "Preview — not yet
 * connected" empty state instead of fabricated rows/canvases/sparklines.
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
  ExternalLink, AlertTriangle, RefreshCw, X, GitBranch, Sparkles,
} from 'lucide-react';

import { knowledgeGraphApi, type KGNode } from '@/lib/api/client';
import { KnowledgeGraph, type KnowledgeNode, type KnowledgeEdge } from '@/components/knowledge/KnowledgeGraph';
import { EmptyState } from '@/components/ui/EmptyState';
import { Skeleton } from '@/components/ui/Skeleton';

// ── Spring constants ──────────────────────────────────────────────────────────

const SPRING_FAST  = { type: 'spring', stiffness: 600, damping: 35 } as const;
const SPRING_NODE  = { type: 'spring', stiffness: 400, damping: 30 } as const;
const SPRING_PANEL = { type: 'spring', stiffness: 280, damping: 26 } as const;

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
      initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
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
  const reduce = useReducedMotion();
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
          <motion.div key={folder}
            initial={reduce ? {} : { opacity: 0, x: -6 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ ...SPRING_NODE, delay: fi * 0.04 }}
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
                    <motion.div key={n.node_id}
                      initial={reduce ? {} : { opacity: 0, x: -4 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ ...SPRING_FAST, delay: ni * 0.04 }}
                      className="flex items-center gap-1.5 px-2 py-1 rounded-lg text-[11px] text-[#64748B] hover:text-[#F1F5F9] hover:bg-[#1A1F2E] cursor-pointer"
                    >
                      <FileCode className="h-3 w-3 text-blue-400" aria-hidden />
                      <span className="truncate">{n.label}</span>
                      <span className="ml-auto text-[10px] text-[#374151] tabular-nums shrink-0">
                        {Math.round(n.confidence * 100)}%
                      </span>
                    </motion.div>
                  ))}
                </motion.div>
              )}
            </AnimatePresence>
          </motion.div>
        ))}
      </div>
    </div>
  );
}

// ── Preview placeholder (Bases / Maps / Timeline) ─────────────────────────────
//
// HONESTY RULE: there is no backend source for Obsidian Bases, JSON Canvas maps,
// or vault-growth history yet (no `/obsidian/*` API, no vault-store endpoints).
// Rather than render fabricated rows/canvases/sparklines, these tabs show an
// explicit "Preview — not yet connected" state naming the missing backend.

function PreviewPlaceholder({ feature, backend }: { feature: string; backend: string }) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      initial={reduce ? {} : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={SPRING_PANEL}
      role="status"
      className="flex flex-col items-center justify-center gap-2 py-10 px-4 text-center bg-[#0F1117] border border-dashed border-[#2D3748] rounded-xl"
    >
      <div className="w-8 h-8 rounded-lg bg-violet-500/10 flex items-center justify-center">
        <Sparkles className="h-4 w-4 text-violet-400" aria-hidden />
      </div>
      <p className="text-[12px] font-medium text-[#F1F5F9]">{feature} — preview, not yet connected</p>
      <p className="text-[11px] text-[#64748B] max-w-xs leading-relaxed">
        This view will light up once the {backend} backend exists. It intentionally
        shows nothing rather than fabricated data.
      </p>
    </motion.div>
  );
}

// ── Main Component ─────────────────────────────────────────────────────────────

interface ObsidianVaultExplorerProps {
  orgId:    string;
  compact?: boolean;
}

export function ObsidianVaultExplorer({ orgId: _orgId, compact = false }: ObsidianVaultExplorerProps) {
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
          initial={reduce ? { opacity: 0 } : { opacity: 0, x: 10 }}
          animate={{ opacity: 1, x: 0 }}
          exit={reduce ? { opacity: 0 } : { opacity: 0, x: -10 }}
          transition={SPRING_PANEL}
        >
          {activeTab === 'graph'    && <GraphView />}
          {activeTab === 'files'    && <FileTree />}
          {activeTab === 'bases'    && <PreviewPlaceholder feature="Bases" backend="Obsidian-Bases table" />}
          {activeTab === 'maps'     && <PreviewPlaceholder feature="Maps" backend="JSON Canvas store" />}
          {activeTab === 'timeline' && <PreviewPlaceholder feature="Timeline" backend="vault-history" />}
        </motion.div>
      </AnimatePresence>
    </section>
  );
}

export default ObsidianVaultExplorer;
