/**
 * WorkflowToolPalette — left panel with draggable step type tiles.
 *
 * Groups: Entry | AI | Integration | Control | Human | Data | Dev
 * Each tile is draggable onto the canvas.
 * Accessible: keyboard drag not required (mouse-only pattern acceptable for canvas DnD).
 */
import { useState } from 'react';
import { Search } from 'lucide-react';
import {
  NODE_CATEGORIES, NODE_COLORS, NODE_ICONS, NODE_LABELS,
  type NodeType,
} from '../design/tokens';

function PaletteTile({ stepType }: { stepType: string }) {
  const colors = NODE_COLORS[stepType as NodeType] ?? {
    bg: 'bg-zinc-500/15', border: 'border-zinc-400/40', text: 'text-zinc-300',
  };
  const icon = NODE_ICONS[stepType] ?? '◻';
  const label = NODE_LABELS[stepType] ?? stepType;

  const onDragStart = (e: React.DragEvent) => {
    e.dataTransfer.setData('application/workflow-step-type', stepType);
    e.dataTransfer.effectAllowed = 'move';
  };

  return (
    <div
      draggable
      onDragStart={onDragStart}
      role="button"
      tabIndex={0}
      aria-label={`Drag ${label} step to canvas`}
      className={`
        flex items-center gap-2 px-3 py-2 rounded-lg border cursor-grab active:cursor-grabbing
        ${colors.bg} ${colors.border}
        hover:brightness-110 transition-all select-none
        focus-visible:ring-2 focus-visible:ring-sky-500
      `}
    >
      <span className={`text-base ${colors.text}`} aria-hidden>{icon}</span>
      <span className="text-xs font-medium text-white/80">{label}</span>
    </div>
  );
}

export function WorkflowToolPalette() {
  const [search, setSearch] = useState('');
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const toggle = (cat: string) =>
    setCollapsed((p) => ({ ...p, [cat]: !p[cat] }));

  const filteredCategories = Object.entries(NODE_CATEGORIES)
    .map(([cat, types]) => ({
      cat,
      types: types.filter((t) =>
        !search || t.toLowerCase().includes(search.toLowerCase())
          || NODE_LABELS[t]?.toLowerCase().includes(search.toLowerCase())
      ),
    }))
    .filter(({ types }) => types.length > 0);

  return (
    <aside
      className="w-52 shrink-0 border-r border-white/10 bg-slate-900/80 flex flex-col
                  overflow-y-auto"
      aria-label="Step type palette"
    >
      {/* Search */}
      <div className="sticky top-0 bg-slate-900/95 backdrop-blur-sm px-3 py-2.5
                       border-b border-white/8 z-10">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5
                             text-white/30" aria-hidden />
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search steps…"
            aria-label="Search step types"
            className="w-full pl-7 pr-2 py-1.5 rounded-lg bg-white/5 border border-white/10
                       text-white/80 placeholder-white/30 text-xs focus:outline-none
                       focus:ring-1 focus:ring-sky-500"
          />
        </div>
      </div>

      {/* Categories */}
      <div className="flex-1 px-2 py-3 space-y-4">
        {filteredCategories.map(({ cat, types }) => (
          <div key={cat}>
            <button
              onClick={() => toggle(cat)}
              className="flex items-center justify-between w-full text-xs font-semibold
                         uppercase tracking-wider text-white/35 hover:text-white/60
                         transition-colors mb-2 px-1"
              aria-expanded={!collapsed[cat]}
              aria-controls={`palette-cat-${cat}`}
            >
              {cat}
              <span aria-hidden className="text-white/20">{collapsed[cat] ? '+' : '−'}</span>
            </button>
            {!collapsed[cat] && (
              <div id={`palette-cat-${cat}`} className="space-y-1.5">
                {types.map((t) => (
                  <PaletteTile key={t} stepType={t} />
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </aside>
  );
}
