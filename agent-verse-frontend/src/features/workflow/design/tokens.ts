/**
 * Workflow Engine Design Tokens
 *
 * Single source of truth for all visual properties in the Workflow Automation
 * Engine UI. Consumed by node styles, config panels, and the canvas.
 *
 * Color philosophy:
 *   - Each step type has a unique identity color (background tint + border + text)
 *   - Status colors follow a universal traffic-light system
 *   - Dark-mode first — all values target dark backgrounds
 */

// ── Node type colors ─────────────────────────────────────────────────────────

export const NODE_COLORS = {
  // Trigger (entry point)
  trigger:      { bg: 'bg-emerald-500/15', border: 'border-emerald-400/60', text: 'text-emerald-300' },
  // AI / LLM
  llm:          { bg: 'bg-violet-500/20',  border: 'border-violet-400/60',  text: 'text-violet-300' },
  rag:          { bg: 'bg-teal-500/15',    border: 'border-teal-400/60',    text: 'text-teal-300' },
  // Integration
  tool:         { bg: 'bg-sky-500/15',     border: 'border-sky-400/60',     text: 'text-sky-300' },
  http:         { bg: 'bg-indigo-500/15',  border: 'border-indigo-400/60',  text: 'text-indigo-300' },
  // Control flow
  conditional:  { bg: 'bg-amber-500/15',   border: 'border-amber-400/60',   text: 'text-amber-300' },
  parallel:     { bg: 'bg-orange-500/15',  border: 'border-orange-400/60',  text: 'text-orange-300' },
  foreach:      { bg: 'bg-cyan-500/15',    border: 'border-cyan-400/60',    text: 'text-cyan-300' },
  // Human
  hitl:         { bg: 'bg-rose-500/15',    border: 'border-rose-400/60',    text: 'text-rose-300' },
  // Data
  transform:    { bg: 'bg-green-500/15',   border: 'border-green-400/60',   text: 'text-green-300' },
  set_variable: { bg: 'bg-yellow-500/15',  border: 'border-yellow-400/60',  text: 'text-yellow-300' },
  // Other
  sub_workflow: { bg: 'bg-slate-500/15',   border: 'border-slate-400/60',   text: 'text-slate-300' },
  wait:         { bg: 'bg-zinc-500/15',    border: 'border-zinc-400/60',    text: 'text-zinc-300' },
  code:         { bg: 'bg-fuchsia-500/15', border: 'border-fuchsia-400/60', text: 'text-fuchsia-300' },
  emit_event:   { bg: 'bg-blue-500/15',    border: 'border-blue-400/60',    text: 'text-blue-300' },
} as const;

export type NodeType = keyof typeof NODE_COLORS;

// ── Node type icons ──────────────────────────────────────────────────────────

export const NODE_ICONS: Record<NodeType | string, string> = {
  trigger:      '▶',
  llm:          '🧠',
  rag:          '🔍',
  tool:         '🔧',
  http:         '🌐',
  conditional:  '◇',
  parallel:     '⫸',
  foreach:      '↺',
  hitl:         '👤',
  transform:    '⇄',
  set_variable: '≔',
  sub_workflow: '⧉',
  wait:         '⏱',
  code:         '</>',
  emit_event:   '📡',
};

// ── Node type display names ───────────────────────────────────────────────────

export const NODE_LABELS: Record<NodeType | string, string> = {
  trigger:      'Trigger',
  llm:          'LLM Prompt',
  rag:          'RAG Retrieval',
  tool:         'MCP Tool',
  http:         'HTTP Request',
  conditional:  'Condition',
  parallel:     'Parallel',
  foreach:      'For Each',
  hitl:         'Human Review',
  transform:    'Transform',
  set_variable: 'Set Variable',
  sub_workflow: 'Sub-Workflow',
  wait:         'Wait',
  code:         'Code',
  emit_event:   'Emit Event',
};

// ── Node categories (for palette) ────────────────────────────────────────────

export const NODE_CATEGORIES: Record<string, (NodeType | string)[]> = {
  'Entry':       ['trigger'],
  'AI':          ['llm', 'rag'],
  'Integration': ['tool', 'http'],
  'Control':     ['conditional', 'parallel', 'foreach', 'wait'],
  'Human':       ['hitl'],
  'Data':        ['transform', 'set_variable'],
  'Dev':         ['code', 'sub_workflow', 'emit_event'],
};

// ── Node shapes ───────────────────────────────────────────────────────────────

export const NODE_SHAPES: Record<NodeType | string, 'rect' | 'diamond' | 'pill' | 'fork' | 'clock'> = {
  trigger:      'pill',
  llm:          'rect',
  rag:          'rect',
  tool:         'rect',
  http:         'rect',
  conditional:  'diamond',
  parallel:     'fork',
  foreach:      'rect',
  hitl:         'rect',
  transform:    'rect',
  set_variable: 'rect',
  sub_workflow: 'rect',
  wait:         'clock',
  code:         'rect',
  emit_event:   'rect',
};

// ── Run status colors ────────────────────────────────────────────────────────

export const STATUS_COLORS = {
  pending:       { bg: 'bg-zinc-500/20',   text: 'text-zinc-400',   dot: 'bg-zinc-400' },
  running:       { bg: 'bg-sky-500/20',    text: 'text-sky-400',    dot: 'bg-sky-400' },
  waiting_hitl:  { bg: 'bg-rose-500/20',   text: 'text-rose-400',   dot: 'bg-rose-400' },
  paused:        { bg: 'bg-amber-500/20',  text: 'text-amber-400',  dot: 'bg-amber-400' },
  complete:      { bg: 'bg-emerald-500/20', text: 'text-emerald-400', dot: 'bg-emerald-400' },
  failed:        { bg: 'bg-red-500/20',    text: 'text-red-400',    dot: 'bg-red-400' },
  cancelled:     { bg: 'bg-zinc-500/15',   text: 'text-zinc-500',   dot: 'bg-zinc-500' },
  timed_out:     { bg: 'bg-orange-500/20', text: 'text-orange-400', dot: 'bg-orange-400' },
} as const;

// ── Priority colors (HITL) ────────────────────────────────────────────────────

export const PRIORITY_COLORS = {
  critical: { bg: 'bg-red-500/20',    text: 'text-red-300',    border: 'border-red-500/50' },
  high:     { bg: 'bg-orange-500/20', text: 'text-orange-300', border: 'border-orange-500/50' },
  medium:   { bg: 'bg-amber-500/20',  text: 'text-amber-300',  border: 'border-amber-500/50' },
  low:      { bg: 'bg-zinc-500/15',   text: 'text-zinc-400',   border: 'border-zinc-500/30' },
} as const;

// ── Typography ───────────────────────────────────────────────────────────────

export const FONTS = {
  sans: '"Inter", ui-sans-serif, system-ui, sans-serif',
  mono: '"JetBrains Mono", "Fira Code", ui-monospace, monospace',
} as const;

// ── Shadows ───────────────────────────────────────────────────────────────────

export const SHADOWS = {
  sm:   'shadow-sm shadow-black/20',
  md:   'shadow-md shadow-black/30',
  lg:   'shadow-lg shadow-black/40',
  glow: 'shadow-lg shadow-sky-500/20',
} as const;

// ── Z-index stack ─────────────────────────────────────────────────────────────

export const Z = {
  canvas:    0,
  node:      10,
  edge:      5,
  minimap:   20,
  toolbar:   30,
  panel:     40,
  modal:     50,
  toast:     60,
  tooltip:   70,
} as const;

// ── Canvas defaults ───────────────────────────────────────────────────────────

export const CANVAS = {
  nodeWidth:  200,
  nodeHeight: 60,
  gridSize:   20,
  snapToGrid: true,
  minZoom:    0.1,
  maxZoom:    2.5,
  defaultZoom: 0.8,
  panOnDrag:  true,
} as const;

// ── Helper ───────────────────────────────────────────────────────────────────

export function getNodeClasses(nodeType: string): string {
  const colors = NODE_COLORS[nodeType as NodeType] ?? {
    bg: 'bg-zinc-500/15', border: 'border-zinc-400/40', text: 'text-zinc-300',
  };
  return `${colors.bg} ${colors.border} ${colors.text}`;
}

export function getStatusClasses(status: string): string {
  const s = STATUS_COLORS[status as keyof typeof STATUS_COLORS] ?? STATUS_COLORS.pending;
  return `${s.bg} ${s.text}`;
}
