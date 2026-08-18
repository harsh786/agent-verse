/**
 * ObsidianVaultExplorer — T7: In-app Obsidian vault exploration panel.
 *
 * Tabs:
 *   Graph View  — animated knowledge graph (same JARVIS style as GraphifyProgress)
 *   File Tree   — hierarchical note explorer with search
 *   Bases       — live Obsidian-Bases table views (missions, tasks)
 *   Maps        — JSON Canvas thumbnails → launch CanvasMapViewer
 *   Timeline    — knowledge growth sparkline per day
 *
 * Skills:
 *   frontend-design:   JARVIS dark vault, emerald-400 note glow, pulsing graph
 *   emil-design-eng:   spring 400/30 tab switch, 280/26 node entrance, stagger 40ms
 *   impeccable-ui:     note title dominant, path secondary, stats tertiary
 *   web-guidelines:    role=tablist, aria-selected, time[datetime], aria-live
 *   ui-ux-pro-max:     44px targets, useReducedMotion, keyboard nav
 */
import { useState, useCallback, useId } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import {
  Network, FileText, BarChart3, Map, Clock,
  Search, ChevronRight, FileCode, Layers,
  ExternalLink, TrendingUp, Circle, CheckCircle2,
} from 'lucide-react';

// ── Types ─────────────────────────────────────────────────────────────────────

interface VaultNode {
  id:       string;
  label:    string;
  type:     'note' | 'folder' | 'canvas' | 'base';
  path:     string;
  x:        number;
  y:        number;
  tags:     string[];
  modified: string;
}

interface VaultEdge { from: string; to: string; label?: string }
interface BaseRow   { title: string; priority: 'HIGH' | 'MEDIUM' | 'LOW'; owner: string; runningFor: string }
interface BaseTable { name: string; rows: BaseRow[]; cost: string }
interface CanvasThumb { name: string; description: string; nodeCount: number }
interface DayGrowth  { date: string; notesAdded: number }

// ── Spring constants ──────────────────────────────────────────────────────────

const SPRING_FAST  = { type: 'spring', stiffness: 600, damping: 35 } as const;
const SPRING_NODE  = { type: 'spring', stiffness: 400, damping: 30 } as const;
const SPRING_PANEL = { type: 'spring', stiffness: 280, damping: 26 } as const;

// ── Demo data ─────────────────────────────────────────────────────────────────

const DEMO_NODES: VaultNode[] = [
  { id: 'n1', label: 'Q3 Strategy', type: 'note', path: 'strategy/Q3.md', x: 120, y: 80, tags: ['strategy', 'q3'], modified: '2026-08-17' },
  { id: 'n2', label: 'Germany Launch', type: 'note', path: 'ops/germany.md', x: 260, y: 60, tags: ['ops', 'eu'], modified: '2026-08-17' },
  { id: 'n3', label: 'SEBI Compliance', type: 'note', path: 'legal/sebi.md', x: 180, y: 160, tags: ['legal', 'compliance'], modified: '2026-08-16' },
  { id: 'n4', label: 'Competitor Map', type: 'canvas', path: 'maps/competitors.canvas', x: 310, y: 150, tags: ['intel'], modified: '2026-08-15' },
  { id: 'n5', label: 'Mission Deps', type: 'canvas', path: 'maps/mission-deps.canvas', x: 80, y: 200, tags: ['planning'], modified: '2026-08-17' },
  { id: 'n6', label: 'Finance Models', type: 'base', path: 'bases/finance.base', x: 220, y: 230, tags: ['finance'], modified: '2026-08-16' },
];

const DEMO_EDGES: VaultEdge[] = [
  { from: 'n1', to: 'n2', label: 'drives' },
  { from: 'n1', to: 'n3', label: 'requires' },
  { from: 'n3', to: 'n6', label: 'feeds' },
];

const DEMO_BASES: BaseTable[] = [{
  name: 'active-missions.base',
  cost: '$8.40/day avg',
  rows: [
    { title: 'Q3 Revenue Analysis', priority: 'HIGH', owner: 'Maya', runningFor: '3 days' },
    { title: 'SEBI Compliance',     priority: 'HIGH', owner: 'Raj',  runningFor: '1 day' },
    { title: 'Competitor Intel',    priority: 'MEDIUM', owner: 'Team', runningFor: '5 hours' },
  ],
}];

const DEMO_CANVASES: CanvasThumb[] = [
  { name: 'org-map.canvas',              description: 'Full org dependency map',    nodeCount: 24 },
  { name: 'competitor-landscape.canvas', description: 'Competitor analysis',        nodeCount: 12 },
  { name: 'mission-deps.canvas',         description: 'Mission interdependencies',  nodeCount: 8  },
];

const DEMO_TIMELINE: DayGrowth[] = [
  { date: '2026-08-17', notesAdded: 28 },
  { date: '2026-08-16', notesAdded: 18 },
  { date: '2026-08-15', notesAdded: 25 },
  { date: '2026-08-14', notesAdded: 14 },
  { date: '2026-08-13', notesAdded: 32 },
  { date: '2026-08-12', notesAdded: 21 },
  { date: '2026-08-11', notesAdded: 9  },
];

// ── Tab types ─────────────────────────────────────────────────────────────────

type VaultTab = 'graph' | 'files' | 'bases' | 'maps' | 'timeline';

const TABS: { id: VaultTab; label: string; icon: React.ComponentType<{className?: string}> }[] = [
  { id: 'graph',    label: 'Graph',    icon: Network    },
  { id: 'files',    label: 'Files',    icon: FileText   },
  { id: 'bases',    label: 'Bases',    icon: BarChart3  },
  { id: 'maps',     label: 'Maps',     icon: Map        },
  { id: 'timeline', label: 'Timeline', icon: Clock      },
];

// ── Graph View ────────────────────────────────────────────────────────────────

function GraphView({ nodes, edges }: { nodes: VaultNode[]; edges: VaultEdge[] }) {
  const reduce = useReducedMotion();
  const [hovered, setHovered] = useState<string | null>(null);

  const NODE_COLORS: Record<string, string> = {
    note: '#60a5fa',   // blue
    canvas: '#a78bfa', // violet
    base: '#34d399',   // emerald
    folder: '#fb923c', // orange
  };

  return (
    <div className="relative h-72 bg-[#090C12] rounded-xl overflow-hidden border border-[#1E2535]" aria-label="Knowledge graph view">
      {/* Ambient grid */}
      <div
        className="absolute inset-0 opacity-10"
        style={{
          backgroundImage: 'radial-gradient(circle, #2D3748 1px, transparent 1px)',
          backgroundSize: '24px 24px',
        }}
      />

      {/* Edges */}
      <svg className="absolute inset-0 w-full h-full pointer-events-none">
        {edges.map((e, i) => {
          const from = nodes.find(n => n.id === e.from);
          const to   = nodes.find(n => n.id === e.to);
          if (!from || !to) return null;
          return (
            <motion.line
              key={i}
              initial={reduce ? {} : { pathLength: 0, opacity: 0 }}
              animate={{ pathLength: 1, opacity: 0.35 }}
              transition={{ ...SPRING_PANEL, delay: i * 0.1 }}
              x1={from.x} y1={from.y} x2={to.x} y2={to.y}
              stroke="#4B5563" strokeWidth={1.5} strokeDasharray="4 3"
            />
          );
        })}
      </svg>

      {/* Nodes */}
      {nodes.map((node, i) => {
        const color = NODE_COLORS[node.type] ?? '#9CA3AF';
        const isHov = hovered === node.id;
        return (
          <motion.button
            key={node.id}
            initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.4 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ ...SPRING_NODE, delay: i * 0.06 }}
            whileHover={reduce ? {} : { scale: 1.15 }}
            onHoverStart={() => setHovered(node.id)}
            onHoverEnd={() => setHovered(null)}
            style={{ left: node.x, top: node.y, touchAction: 'manipulation' }}
            aria-label={`Vault node: ${node.label} (${node.type})`}
            className="absolute -translate-x-1/2 -translate-y-1/2 flex flex-col items-center gap-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 rounded"
          >
            <motion.div
              animate={!reduce ? { boxShadow: [`0 0 6px ${color}40`, `0 0 16px ${color}80`, `0 0 6px ${color}40`] } : {}}
              transition={{ duration: 2, repeat: Infinity, delay: i * 0.3 }}
              style={{ backgroundColor: `${color}20`, borderColor: color }}
              className="w-8 h-8 rounded-full border-2 flex items-center justify-center"
            >
              {node.type === 'canvas' ? <Map className="h-3.5 w-3.5" style={{ color }} aria-hidden /> :
               node.type === 'base'   ? <BarChart3 className="h-3.5 w-3.5" style={{ color }} aria-hidden /> :
                                        <FileText className="h-3.5 w-3.5" style={{ color }} aria-hidden />}
            </motion.div>
            <AnimatePresence>
              {isHov && (
                <motion.span
                  initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
                  transition={SPRING_FAST}
                  className="text-[10px] font-medium text-[#F1F5F9] bg-[#1A1F2E] px-2 py-0.5 rounded-full border border-[#2D3748] whitespace-nowrap pointer-events-none z-10"
                >
                  {node.label}
                </motion.span>
              )}
            </AnimatePresence>
          </motion.button>
        );
      })}

      {/* Stats overlay */}
      <div className="absolute bottom-3 left-3 flex items-center gap-3 text-[10px] text-[#475569]">
        <span className="tabular-nums">{nodes.length} nodes</span>
        <span>·</span>
        <span className="tabular-nums">{edges.length} edges</span>
      </div>
    </div>
  );
}

// ── File Tree ─────────────────────────────────────────────────────────────────

function FileTree({ nodes }: { nodes: VaultNode[] }) {
  const reduce = useReducedMotion();
  const [expanded, setExpanded] = useState<Set<string>>(new Set(['strategy', 'ops']));
  const [search, setSearch] = useState('');

  const filtered = nodes.filter(n =>
    !search || n.label.toLowerCase().includes(search.toLowerCase())
  );

  const folders = [...new Set(filtered.map(n => n.path.split('/')[0]))];

  return (
    <div className="space-y-2">
      <div className="relative">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[#475569]" aria-hidden />
        <input
          type="search" value={search} onChange={e => setSearch(e.target.value)}
          placeholder="Search vault…"
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
              aria-label={`${expanded.has(folder) ? 'Collapse' : 'Expand'} ${folder} folder`}
              className="flex items-center gap-1.5 w-full text-left px-2 py-1 rounded-lg text-[12px] text-[#94A3B8] hover:bg-[#1A1F2E] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
            >
              <motion.div animate={{ rotate: expanded.has(folder) ? 90 : 0 }} transition={SPRING_FAST}>
                <ChevronRight className="h-3.5 w-3.5" aria-hidden />
              </motion.div>
              <Layers className="h-3.5 w-3.5 text-amber-400" aria-hidden />
              <span>{folder}/</span>
            </button>
            <AnimatePresence>
              {expanded.has(folder) && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }} transition={SPRING_PANEL}
                  style={{ overflow: 'hidden' }} className="pl-6"
                >
                  {filtered.filter(n => n.path.startsWith(folder + '/')).map((n, ni) => (
                    <motion.div key={n.id}
                      initial={reduce ? {} : { opacity: 0, x: -4 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ ...SPRING_FAST, delay: ni * 0.04 }}
                      className="flex items-center gap-1.5 px-2 py-1 rounded-lg text-[11px] text-[#64748B] hover:text-[#F1F5F9] hover:bg-[#1A1F2E] cursor-pointer"
                    >
                      {n.type === 'canvas' ? <Map className="h-3 w-3 text-violet-400" aria-hidden /> :
                       n.type === 'base'   ? <BarChart3 className="h-3 w-3 text-emerald-400" aria-hidden /> :
                                            <FileCode className="h-3 w-3 text-blue-400" aria-hidden />}
                      <span>{n.label}</span>
                      <span className="ml-auto text-[10px] text-[#374151] tabular-nums">
                        {new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric' }).format(new Date(n.modified))}
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

// ── Bases Tab ─────────────────────────────────────────────────────────────────

const PRIORITY_COLORS = { HIGH: 'text-red-400 bg-red-500/10', MEDIUM: 'text-amber-400 bg-amber-500/10', LOW: 'text-[#64748B] bg-[#252B3B]' };

function BasesTab({ tables }: { tables: BaseTable[] }) {
  const reduce = useReducedMotion();
  return (
    <div className="space-y-3">
      {tables.map((table, ti) => (
        <motion.div key={table.name}
          initial={reduce ? {} : { opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ ...SPRING_PANEL, delay: ti * 0.06 }}
          className="bg-[#0F1117] border border-[#2D3748] rounded-xl overflow-hidden"
        >
          <div className="flex items-center justify-between px-3 py-2 border-b border-[#1E2535]">
            <div className="flex items-center gap-1.5">
              <BarChart3 className="h-3.5 w-3.5 text-emerald-400" aria-hidden />
              <span className="text-[12px] font-medium text-[#F1F5F9]">{table.name}</span>
            </div>
            <button aria-label={`Open ${table.name} in Obsidian`}
              className="flex items-center gap-1 text-[10px] text-[#475569] hover:text-[#94A3B8] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 rounded">
              <ExternalLink className="h-3 w-3" aria-hidden />Open
            </button>
          </div>
          <table className="w-full text-[11px]" aria-label={table.name}>
            <thead>
              <tr className="border-b border-[#1E2535]">
                {['Title', 'Priority', 'Owner', 'Running For'].map(h => (
                  <th key={h} className="text-left px-3 py-1.5 text-[#475569] font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((row, ri) => (
                <motion.tr key={ri}
                  initial={reduce ? {} : { opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ ...SPRING_FAST, delay: ti * 0.06 + ri * 0.04 }}
                  className="border-b border-[#0D1117] hover:bg-[#1A1F2E] transition-colors"
                >
                  <td className="px-3 py-2 text-[#E2E8F0]">{row.title}</td>
                  <td className="px-3 py-2">
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${PRIORITY_COLORS[row.priority]}`}>{row.priority}</span>
                  </td>
                  <td className="px-3 py-2 text-[#94A3B8]">{row.owner}</td>
                  <td className="px-3 py-2 text-[#64748B] tabular-nums">{row.runningFor}</td>
                </motion.tr>
              ))}
            </tbody>
          </table>
          <div className="px-3 py-2 text-[10px] text-[#475569]">
            {table.rows.length} missions · {table.cost}
          </div>
        </motion.div>
      ))}
    </div>
  );
}

// ── Maps Tab ──────────────────────────────────────────────────────────────────

function MapsTab({ canvases, onOpen }: { canvases: CanvasThumb[]; onOpen: (c: CanvasThumb) => void }) {
  const reduce = useReducedMotion();
  return (
    <div className="grid gap-2">
      {canvases.map((c, i) => (
        <motion.button
          key={c.name}
          initial={reduce ? {} : { opacity: 0, x: -8 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ ...SPRING_NODE, delay: i * 0.06 }}
          whileHover={reduce ? {} : { x: 3 }}
          whileTap={reduce ? {} : { scale: 0.98 }}
          onClick={() => onOpen(c)}
          aria-label={`Open canvas: ${c.name}`}
          style={{ touchAction: 'manipulation' }}
          className="flex items-center gap-3 p-3 bg-[#0F1117] border border-[#2D3748] rounded-xl text-left hover:border-violet-500/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/70 group"
        >
          <div className="w-10 h-10 rounded-lg bg-violet-500/10 border border-violet-500/20 flex items-center justify-center flex-shrink-0">
            <Map className="h-5 w-5 text-violet-400" aria-hidden />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-[12px] font-medium text-[#F1F5F9] truncate">{c.name}</p>
            <p className="text-[11px] text-[#64748B]">{c.description}</p>
          </div>
          <div className="text-right flex-shrink-0">
            <p className="text-[11px] text-[#475569] tabular-nums">{c.nodeCount} nodes</p>
            <ChevronRight className="h-3.5 w-3.5 text-[#374151] group-hover:text-violet-400 transition-colors mt-0.5 ml-auto" aria-hidden />
          </div>
        </motion.button>
      ))}
    </div>
  );
}

// ── Timeline Tab ──────────────────────────────────────────────────────────────

function TimelineTab({ days }: { days: DayGrowth[] }) {
  const reduce = useReducedMotion();
  const max    = Math.max(...days.map(d => d.notesAdded), 1);
  return (
    <div className="space-y-2" aria-label="Knowledge growth timeline">
      <div className="flex items-center gap-2 mb-3">
        <TrendingUp className="h-4 w-4 text-emerald-400" aria-hidden />
        <span className="text-[12px] font-medium text-[#F1F5F9]">Knowledge Growth</span>
        <span className="text-[11px] text-[#64748B] ml-auto tabular-nums">
          {days.reduce((s, d) => s + d.notesAdded, 0)} notes this week
        </span>
      </div>
      {days.map((day, i) => (
        <div key={day.date} className="flex items-center gap-3">
          <time dateTime={day.date} className="text-[10px] text-[#475569] tabular-nums w-14 flex-shrink-0">
            {new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric' }).format(new Date(day.date))}
          </time>
          <div className="flex-1 h-5 bg-[#1A1F2E] rounded-full overflow-hidden" role="meter" aria-valuenow={day.notesAdded} aria-valuemax={max} aria-label={`${day.notesAdded} notes added`}>
            <motion.div
              initial={{ width: 0 }}
              animate={{ width: `${(day.notesAdded / max) * 100}%` }}
              transition={reduce ? { duration: 0 } : { ...SPRING_PANEL, delay: i * 0.05 }}
              className="h-full bg-gradient-to-r from-emerald-600 to-emerald-400 rounded-full"
            />
          </div>
          <span className="text-[11px] text-[#64748B] tabular-nums w-8 text-right">{day.notesAdded}</span>
        </div>
      ))}
    </div>
  );
}

// ── Main Component ─────────────────────────────────────────────────────────────

interface ObsidianVaultExplorerProps {
  orgId:    string;
  onOpenCanvas?: (name: string) => void;
  compact?: boolean;
}

export function ObsidianVaultExplorer({ orgId: _orgId, onOpenCanvas, compact = false }: ObsidianVaultExplorerProps) {
  const labelId  = useId();
  const reduce   = useReducedMotion();
  const [activeTab, setActiveTab] = useState<VaultTab>('graph');
  const [openCanvas, setOpenCanvas] = useState<CanvasThumb | null>(null);

  const handleOpenCanvas = useCallback((c: CanvasThumb) => {
    setOpenCanvas(c);
    onOpenCanvas?.(c.name);
  }, [onOpenCanvas]);

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
          {activeTab === 'graph'    && <GraphView nodes={DEMO_NODES} edges={DEMO_EDGES} />}
          {activeTab === 'files'    && <FileTree nodes={DEMO_NODES} />}
          {activeTab === 'bases'    && <BasesTab tables={DEMO_BASES} />}
          {activeTab === 'maps'     && <MapsTab canvases={DEMO_CANVASES} onOpen={handleOpenCanvas} />}
          {activeTab === 'timeline' && <TimelineTab days={DEMO_TIMELINE} />}
        </motion.div>
      </AnimatePresence>

      {/* Canvas viewer overlay */}
      <AnimatePresence>
        {openCanvas && (
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="mt-3 p-3 bg-[#0F1117] border border-violet-500/30 rounded-xl"
            aria-label={`Canvas viewer: ${openCanvas.name}`}
          >
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <Map className="h-3.5 w-3.5 text-violet-400" aria-hidden />
                <span className="text-[12px] font-medium text-[#F1F5F9]">{openCanvas.name}</span>
              </div>
              <button onClick={() => setOpenCanvas(null)} aria-label="Close canvas preview"
                className="text-[#475569] hover:text-[#94A3B8] text-[10px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 rounded">
                Close
              </button>
            </div>
            <div className="h-32 bg-[#090C12] rounded-lg border border-[#1E2535] flex items-center justify-center">
              <div className="text-center">
                <p className="text-[12px] text-[#475569]">{openCanvas.description}</p>
                <p className="text-[11px] text-[#374151] mt-1 tabular-nums">{openCanvas.nodeCount} nodes</p>
                <motion.div
                  animate={reduce ? {} : { scale: [1, 1.05, 1] }}
                  transition={{ duration: 2, repeat: Infinity }}
                  className="mt-2 flex items-center justify-center gap-1"
                >
                  <Circle className="h-3 w-3 text-blue-400 fill-blue-400/20" aria-hidden />
                  <Circle className="h-3 w-3 text-violet-400 fill-violet-400/20" aria-hidden />
                  <CheckCircle2 className="h-3 w-3 text-emerald-400" aria-hidden />
                </motion.div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}

export default ObsidianVaultExplorer;
