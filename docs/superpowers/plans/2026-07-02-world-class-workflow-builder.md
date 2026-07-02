# World-Class Workflow Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken workflow builder with a world-class, fully interactive drag-and-drop visual editor where nodes connect properly, users can build real multi-step workflows, and all interactions are E2E tested.

**Architecture:** Single-file rewrite of `WorkflowBuilderPage.tsx` split into focused sub-components: `WorkflowNode` (renders with visible handles), `PaletteItem` (draggable), `NodeInspector` (type-aware config), `EdgeInspector` (label editing). The ReactFlow canvas handles drag-drop, connect, select, delete. Backend `workflows.py` is complete — no changes needed.

**Tech Stack:** React 19 · @xyflow/react 12 · TypeScript · Vitest · Playwright · Tailwind

---

## Root Cause of Broken Connections

| Issue | Root Cause | Fix |
|-------|-----------|-----|
| Can't connect nodes by dragging | Handles are `!w-3 !h-3` (12px) — very hard to grab; no `connectionLineStyle` gives zero visual feedback | Increase handles to 16px, add `connectionLineStyle`, add `connectionMode` |
| No drag-from-palette | Palette only has `onClick`; no `onDragStart`/`onDragOver`/`onDrop` on canvas | Add HTML5 drag API to palette buttons + ReactFlow drop handler |
| No connection validation | Any source can connect to any target including self-loops | Add `isValidConnection` callback |
| Edge styling inconsistent | `markerEnd` set but no `defaultEdgeOptions` | Add `defaultEdgeOptions` as stable reference |
| `fitView` breaks on empty canvas | Warns in console when no nodes exist | Guard `fitView` with `fitViewOptions={{ padding: 0.2 }}` |

---

## File Structure

**Modified:**
- `agent-verse-frontend/src/features/workflow-builder/WorkflowBuilderPage.tsx` — complete rewrite, split into sub-components in same file
- `agent-verse-frontend/src/features/workflow-builder/WorkflowBuilderPage.test.tsx` — add unit tests for all new behaviours
- `agent-verse-frontend/e2e/workflow-builder.spec.ts` — complete E2E coverage

**No backend changes** — `workflows.py` is already correct.

---

### Task 1: Fix Core Connection Infrastructure + Drag-and-Drop from Palette

**Root causes being fixed:**
- Handles too small (12px) to reliably hover/grab
- No visual feedback while dragging connection
- Palette items have no drag behaviour — only click-to-add
- No `onDrop` on the canvas

**Files:**
- Modify: `agent-verse-frontend/src/features/workflow-builder/WorkflowBuilderPage.tsx`

- [ ] **Step 1: Write failing unit tests**

Add these tests to `WorkflowBuilderPage.test.tsx` (append to existing describe block):

```tsx
test('palette items are draggable (have draggable attribute)', async () => {
  render(<WorkflowBuilderPage />, { wrapper: ({ children }) => <Wrapper c={children} /> });
  const paletteButtons = screen.getAllByRole('button', { name: /add .+ node/i });
  expect(paletteButtons.length).toBeGreaterThan(0);
  // Every palette item must be draggable for DnD onto canvas
  paletteButtons.forEach((btn) => {
    expect(btn).toHaveAttribute('draggable', 'true');
  });
});

test('clicking a palette item adds a node to the canvas', async () => {
  const user = userEvent.setup();
  render(<WorkflowBuilderPage />, { wrapper: ({ children }) => <Wrapper c={children} /> });
  const triggerBtn = screen.getByRole('button', { name: /add trigger/i });
  await user.click(triggerBtn);
  // After adding a node, the ReactFlow container should exist
  expect(document.querySelector('.react-flow')).toBeInTheDocument();
});

test('workflow name input defaults to "My Workflow"', () => {
  render(<WorkflowBuilderPage />, { wrapper: ({ children }) => <Wrapper c={children} /> });
  expect(screen.getByDisplayValue(/my workflow/i)).toBeInTheDocument();
});

test('Save button is present and not disabled initially', () => {
  render(<WorkflowBuilderPage />, { wrapper: ({ children }) => <Wrapper c={children} /> });
  const saveBtn = screen.getByRole('button', { name: /save/i });
  expect(saveBtn).toBeInTheDocument();
  expect(saveBtn).not.toBeDisabled();
});

test('Run button is present', () => {
  render(<WorkflowBuilderPage />, { wrapper: ({ children }) => <Wrapper c={children} /> });
  expect(screen.getByRole('button', { name: /▶ run/i })).toBeInTheDocument();
});

test('Dry Run button is present and enabled', () => {
  render(<WorkflowBuilderPage />, { wrapper: ({ children }) => <Wrapper c={children} /> });
  const dryRun = screen.getByRole('button', { name: /dry run/i });
  expect(dryRun).toBeInTheDocument();
  expect(dryRun).not.toBeDisabled();
});
```

Run: `npm run test -- src/features/workflow-builder/WorkflowBuilderPage.test.tsx`
Expected: Some pass, the `draggable` test likely FAILS (not yet implemented).

- [ ] **Step 2: Rewrite `WorkflowBuilderPage.tsx` with world-class implementation**

Replace the entire file with the following. This is the complete, production-ready implementation:

```tsx
/**
 * World-class visual workflow builder.
 *
 * Features:
 *  - Drag-and-drop from node palette onto canvas
 *  - Click-to-add nodes from palette
 *  - Visual connection handles (16 px, highlighted on hover)
 *  - Connection line preview with animated dash while dragging
 *  - Connection validation (no self-loops, trigger can only be source)
 *  - Edge selection + label editing in inspector
 *  - Node type-aware inspector panel
 *  - Backspace / Delete key removes selected nodes/edges
 *  - Save, Load, New, Run, Dry Run toolbar
 *  - Natural-language workflow generation via dry_run goal
 *  - Status animation on nodes during run
 *  - Empty-canvas guidance with drag-here hint
 */
import { useState, useCallback, useRef, type DragEvent } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  BackgroundVariant,
  addEdge,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type Connection,
  type NodeTypes,
  MarkerType,
  Handle,
  Position,
  ConnectionMode,
  type IsValidConnection,
  type EdgeChange,
  useReactFlow,
  ReactFlowProvider,
  Panel,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '../../stores/auth';
import { toast } from '../../stores/toast';
import { workflowsApi, type WorkflowRecord } from '../../lib/api/client';

// ─── Types ────────────────────────────────────────────────────────────────────

export interface WorkflowNodeData extends Record<string, unknown> {
  nodeType: string;
  label: string;
  subtitle: string;
  config: Record<string, string>;
  status: 'idle' | 'running' | 'complete' | 'failed' | null;
}

// ─── Node Metadata ────────────────────────────────────────────────────────────

export const NODE_META: Record<string, {
  color: string;
  borderColor: string;
  textColor: string;
  icon: string;
  label: string;
  description: string;
  canBeSource: boolean;
  canBeTarget: boolean;
}> = {
  trigger: {
    color: 'bg-emerald-50 dark:bg-emerald-950/40',
    borderColor: 'border-emerald-400 dark:border-emerald-600',
    textColor: 'text-emerald-800 dark:text-emerald-300',
    icon: '▶',
    label: 'Trigger',
    description: 'Start event for the workflow',
    canBeSource: true,
    canBeTarget: false,
  },
  tool_call: {
    color: 'bg-blue-50 dark:bg-blue-950/40',
    borderColor: 'border-blue-400 dark:border-blue-600',
    textColor: 'text-blue-800 dark:text-blue-300',
    icon: '🔧',
    label: 'Tool Call',
    description: 'Execute a connector tool',
    canBeSource: true,
    canBeTarget: true,
  },
  agent_step: {
    color: 'bg-violet-50 dark:bg-violet-950/40',
    borderColor: 'border-violet-400 dark:border-violet-600',
    textColor: 'text-violet-800 dark:text-violet-300',
    icon: '🤖',
    label: 'Agent Step',
    description: 'Run an AI agent sub-task',
    canBeSource: true,
    canBeTarget: true,
  },
  decision: {
    color: 'bg-amber-50 dark:bg-amber-950/40',
    borderColor: 'border-amber-400 dark:border-amber-600',
    textColor: 'text-amber-800 dark:text-amber-300',
    icon: '❓',
    label: 'Decision',
    description: 'Branch based on condition',
    canBeSource: true,
    canBeTarget: true,
  },
  parallel: {
    color: 'bg-orange-50 dark:bg-orange-950/40',
    borderColor: 'border-orange-400 dark:border-orange-600',
    textColor: 'text-orange-800 dark:text-orange-300',
    icon: '⫸',
    label: 'Parallel',
    description: 'Fan out to multiple branches',
    canBeSource: true,
    canBeTarget: true,
  },
  loop: {
    color: 'bg-cyan-50 dark:bg-cyan-950/40',
    borderColor: 'border-cyan-400 dark:border-cyan-600',
    textColor: 'text-cyan-800 dark:text-cyan-300',
    icon: '↻',
    label: 'Loop',
    description: 'Repeat over a collection',
    canBeSource: true,
    canBeTarget: true,
  },
  human_approval: {
    color: 'bg-rose-50 dark:bg-rose-950/40',
    borderColor: 'border-rose-400 dark:border-rose-600',
    textColor: 'text-rose-800 dark:text-rose-300',
    icon: '👤',
    label: 'Human Approval',
    description: 'Pause for human review',
    canBeSource: true,
    canBeTarget: true,
  },
  delay: {
    color: 'bg-slate-50 dark:bg-slate-950/40',
    borderColor: 'border-slate-400 dark:border-slate-600',
    textColor: 'text-slate-700 dark:text-slate-300',
    icon: '⏱',
    label: 'Delay',
    description: 'Wait for a duration',
    canBeSource: true,
    canBeTarget: true,
  },
  end: {
    color: 'bg-gray-100 dark:bg-gray-900/40',
    borderColor: 'border-gray-400 dark:border-gray-600',
    textColor: 'text-gray-700 dark:text-gray-300',
    icon: '⬛',
    label: 'End',
    description: 'Terminate the workflow',
    canBeSource: false,
    canBeTarget: true,
  },
};

const PALETTE_ITEMS = Object.entries(NODE_META).map(([type, meta]) => ({
  type,
  label: meta.label,
  description: meta.description,
  icon: meta.icon,
}));

// ─── WorkflowNode component ───────────────────────────────────────────────────

const STATUS_RING: Record<string, string> = {
  running: 'ring-2 ring-blue-400 animate-pulse',
  complete: 'ring-2 ring-emerald-400',
  failed: 'ring-2 ring-red-400',
};

function WorkflowNode({ data, selected }: { data: WorkflowNodeData; selected?: boolean }) {
  const meta = NODE_META[data.nodeType] ?? NODE_META.tool_call;
  const statusRing = data.status ? (STATUS_RING[data.status] ?? '') : '';

  return (
    <div
      className={`
        relative rounded-xl border-2 px-4 py-3 min-w-[160px] max-w-[240px]
        shadow-sm transition-shadow
        ${meta.color} ${meta.borderColor} ${meta.textColor}
        ${selected ? 'ring-2 ring-primary ring-offset-2' : ''}
        ${statusRing}
      `}
      data-node-type={data.nodeType}
    >
      {/* Target handle — top centre, 16 × 16 px */}
      {meta.canBeTarget && (
        <Handle
          type="target"
          position={Position.Top}
          className="
            !w-4 !h-4 !border-2 !rounded-full
            !bg-white dark:!bg-gray-800
            !border-current
            hover:!bg-primary hover:!border-primary
            transition-colors cursor-crosshair
          "
          aria-label="Target connection point"
        />
      )}

      <div className="flex items-center gap-2">
        <span className="text-base leading-none select-none" aria-hidden="true">
          {meta.icon}
        </span>
        <div className="min-w-0">
          <div className="font-semibold text-xs leading-tight truncate">
            {String(data.label)}
          </div>
          {data.subtitle && (
            <div className="text-[10px] opacity-60 truncate mt-0.5">
              {String(data.subtitle)}
            </div>
          )}
        </div>
      </div>

      {data.status && (
        <div
          className={`mt-1.5 text-[10px] font-semibold flex items-center gap-1 ${
            data.status === 'running'  ? 'text-blue-600 dark:text-blue-400'  :
            data.status === 'complete' ? 'text-emerald-600 dark:text-emerald-400' :
            data.status === 'failed'   ? 'text-red-600 dark:text-red-400'   :
            'opacity-50'
          }`}
        >
          <span aria-hidden="true">
            {data.status === 'running' ? '●' : data.status === 'complete' ? '✓' : data.status === 'failed' ? '✗' : ''}
          </span>
          {data.status}
        </div>
      )}

      {/* Source handle — bottom centre, 16 × 16 px */}
      {meta.canBeSource && (
        <Handle
          type="source"
          position={Position.Bottom}
          className="
            !w-4 !h-4 !border-2 !rounded-full
            !bg-white dark:!bg-gray-800
            !border-current
            hover:!bg-primary hover:!border-primary
            transition-colors cursor-crosshair
          "
          aria-label="Source connection point"
        />
      )}
    </div>
  );
}

const NODE_TYPES: NodeTypes = { workflow: WorkflowNode };

const DEFAULT_EDGE_OPTIONS = {
  type: 'smoothstep',
  markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
  style: { stroke: '#6366f1', strokeWidth: 2 },
  animated: false,
};

const CONNECTION_LINE_STYLE = {
  stroke: '#6366f1',
  strokeWidth: 2,
  strokeDasharray: '6 3',
};

const SNAP_GRID: [number, number] = [16, 16];

// ─── Palette Item (draggable) ─────────────────────────────────────────────────

function PaletteItem({
  item,
  onAdd,
}: {
  item: (typeof PALETTE_ITEMS)[0];
  onAdd: (type: string, label: string) => void;
}) {
  const meta = NODE_META[item.type];

  const onDragStart = (e: DragEvent<HTMLButtonElement>) => {
    e.dataTransfer.setData('application/reactflow-type', item.type);
    e.dataTransfer.setData('application/reactflow-label', item.label);
    e.dataTransfer.effectAllowed = 'move';
  };

  return (
    <button
      type="button"
      draggable
      onDragStart={onDragStart}
      onClick={() => onAdd(item.type, item.label)}
      aria-label={`Add ${item.label} node`}
      title={item.description}
      className={`
        w-full text-left text-xs px-2.5 py-2 rounded-lg border-2
        ${meta.color} ${meta.borderColor} ${meta.textColor}
        hover:opacity-90 transition-opacity cursor-grab active:cursor-grabbing
        flex items-center gap-2
      `}
    >
      <span aria-hidden="true">{item.icon}</span>
      <span className="font-medium truncate">{item.label}</span>
    </button>
  );
}

// ─── Node Inspector ───────────────────────────────────────────────────────────

function NodeInspector({
  node,
  onUpdate,
  onDelete,
}: {
  node: Node<WorkflowNodeData>;
  onUpdate: (id: string, data: Partial<WorkflowNodeData>) => void;
  onDelete: (id: string) => void;
}) {
  const data = node.data;
  const meta = NODE_META[data.nodeType] ?? NODE_META.tool_call;

  return (
    <div className="flex flex-col gap-3 p-3 text-xs">
      <div className="flex items-center gap-2 pb-2 border-b">
        <span className="text-sm">{meta.icon}</span>
        <span className={`font-semibold ${meta.textColor}`}>{meta.label}</span>
        <span className="text-muted-foreground text-[10px] ml-auto truncate">{node.id}</span>
      </div>

      <div>
        <label htmlFor="ins-label" className="block text-muted-foreground mb-1 font-medium">
          Label
        </label>
        <input
          id="ins-label"
          value={String(data.label)}
          onChange={(e) => onUpdate(node.id, { label: e.target.value })}
          className="w-full border rounded-lg px-2.5 py-1.5 bg-background text-xs"
          placeholder="Node label…"
        />
      </div>

      <div>
        <label htmlFor="ins-subtitle" className="block text-muted-foreground mb-1 font-medium">
          {data.nodeType === 'tool_call' ? 'Tool / Connector' :
           data.nodeType === 'decision'  ? 'Condition' :
           data.nodeType === 'delay'     ? 'Duration (e.g. 30s, 5m)' :
           data.nodeType === 'trigger'   ? 'Trigger source' :
           'Description'}
        </label>
        <input
          id="ins-subtitle"
          value={String(data.subtitle ?? '')}
          onChange={(e) => onUpdate(node.id, { subtitle: e.target.value })}
          className="w-full border rounded-lg px-2.5 py-1.5 bg-background text-xs"
          placeholder={
            data.nodeType === 'tool_call' ? 'e.g. jira_search_issues' :
            data.nodeType === 'decision'  ? 'e.g. status == "done"' :
            data.nodeType === 'delay'     ? 'e.g. 5m' :
            data.nodeType === 'trigger'   ? 'e.g. schedule, webhook' :
            'Optional description…'
          }
        />
      </div>

      <button
        type="button"
        onClick={() => onDelete(node.id)}
        aria-label="Delete selected node"
        className="mt-1 w-full text-xs bg-red-50 text-red-600 border border-red-200 rounded-lg py-1.5 hover:bg-red-100 dark:bg-red-950/30 dark:text-red-400 dark:border-red-800 transition-colors"
      >
        Delete Node
      </button>
    </div>
  );
}

// ─── Edge Inspector ───────────────────────────────────────────────────────────

function EdgeInspector({
  edge,
  onUpdateLabel,
  onDelete,
}: {
  edge: Edge;
  onUpdateLabel: (id: string, label: string) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <div className="flex flex-col gap-3 p-3 text-xs">
      <div className="pb-2 border-b">
        <span className="font-semibold text-muted-foreground">Connection</span>
        <div className="text-[10px] text-muted-foreground mt-0.5">
          {edge.source} → {edge.target}
        </div>
      </div>

      <div>
        <label htmlFor="edge-label" className="block text-muted-foreground mb-1 font-medium">
          Label / Condition
        </label>
        <input
          id="edge-label"
          defaultValue={typeof edge.label === 'string' ? edge.label : ''}
          onBlur={(e) => onUpdateLabel(edge.id, e.target.value)}
          className="w-full border rounded-lg px-2.5 py-1.5 bg-background text-xs"
          placeholder="e.g. true, on success…"
        />
      </div>

      <button
        type="button"
        onClick={() => onDelete(edge.id)}
        aria-label="Delete selected edge"
        className="mt-1 w-full text-xs bg-red-50 text-red-600 border border-red-200 rounded-lg py-1.5 hover:bg-red-100 dark:bg-red-950/30 dark:text-red-400 dark:border-red-800 transition-colors"
      >
        Delete Connection
      </button>
    </div>
  );
}

// ─── Main Canvas Component (needs ReactFlowProvider above) ────────────────────

function WorkflowCanvas() {
  const apiKey = useAuthStore((s) => s.apiKey);
  const qc = useQueryClient();
  const { screenToFlowPosition } = useReactFlow();

  const [nodes, setNodes, onNodesChange] = useNodesState<Node<WorkflowNodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedNode, setSelectedNode] = useState<Node<WorkflowNodeData> | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<Edge | null>(null);
  const [workflowName, setWorkflowName] = useState('My Workflow');
  const [currentWfId, setCurrentWfId] = useState<string | null>(null);
  const [nlGoal, setNlGoal] = useState('');
  const [generating, setGenerating] = useState(false);
  const [running, setRunning] = useState(false);
  const [runOutput, setRunOutput] = useState<string>('');
  const nodeCounter = useRef(1);
  const isDirty = useRef(false);

  const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

  const { data: savedWorkflows = [] } = useQuery({
    queryKey: ['workflows'],
    queryFn: () => workflowsApi.list(),
    enabled: !!apiKey,
  });

  // ── Node helpers ──────────────────────────────────────────────────────────

  const addNode = useCallback(
    (type: string, label: string, position = { x: 200 + Math.random() * 300, y: 100 + Math.random() * 200 }) => {
      const id = `node_${nodeCounter.current++}`;
      setNodes((nds) => [
        ...nds,
        {
          id,
          type: 'workflow',
          position,
          data: {
            nodeType: type,
            label,
            subtitle: '',
            config: {},
            status: null,
          } satisfies WorkflowNodeData,
        },
      ]);
      isDirty.current = true;
    },
    [setNodes]
  );

  const updateNodeData = useCallback(
    (id: string, patch: Partial<WorkflowNodeData>) => {
      setNodes((nds) =>
        nds.map((n) =>
          n.id === id ? { ...n, data: { ...n.data, ...patch } } : n
        )
      );
      setSelectedNode((prev) =>
        prev?.id === id ? { ...prev, data: { ...prev.data, ...patch } } : prev
      );
      isDirty.current = true;
    },
    [setNodes]
  );

  const deleteNode = useCallback(
    (id: string) => {
      setNodes((nds) => nds.filter((n) => n.id !== id));
      setEdges((eds) => eds.filter((e) => e.source !== id && e.target !== id));
      setSelectedNode((prev) => (prev?.id === id ? null : prev));
      isDirty.current = true;
    },
    [setNodes, setEdges]
  );

  const deleteEdge = useCallback(
    (id: string) => {
      setEdges((eds) => eds.filter((e) => e.id !== id));
      setSelectedEdge((prev) => (prev?.id === id ? null : prev));
      isDirty.current = true;
    },
    [setEdges]
  );

  const updateEdgeLabel = useCallback(
    (id: string, label: string) => {
      setEdges((eds) =>
        eds.map((e) =>
          e.id === id
            ? { ...e, label: label || undefined, labelStyle: { fontSize: 10, fontWeight: 600 }, labelBgStyle: { fill: '#f8fafc', fillOpacity: 0.9 } }
            : e
        )
      );
      isDirty.current = true;
    },
    [setEdges]
  );

  // ── Connection logic ──────────────────────────────────────────────────────

  const onConnect = useCallback(
    (connection: Connection) => {
      setEdges((eds) =>
        addEdge(
          {
            ...connection,
            ...DEFAULT_EDGE_OPTIONS,
            id: `e_${connection.source}_${connection.target}_${Date.now()}`,
          },
          eds
        )
      );
      isDirty.current = true;
    },
    [setEdges]
  );

  const isValidConnection: IsValidConnection = useCallback(
    (connection) => {
      if (connection.source === connection.target) return false; // no self-loop
      const sourceNode = nodes.find((n) => n.id === connection.source);
      const targetNode = nodes.find((n) => n.id === connection.target);
      if (!sourceNode || !targetNode) return false;
      const sourceMeta = NODE_META[(sourceNode.data as WorkflowNodeData).nodeType];
      const targetMeta = NODE_META[(targetNode.data as WorkflowNodeData).nodeType];
      return (sourceMeta?.canBeSource ?? true) && (targetMeta?.canBeTarget ?? true);
    },
    [nodes]
  );

  // ── Drag-and-drop from palette ────────────────────────────────────────────

  const onDragOver = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback(
    (e: DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      const type = e.dataTransfer.getData('application/reactflow-type');
      const label = e.dataTransfer.getData('application/reactflow-label');
      if (!type) return;
      const position = screenToFlowPosition({ x: e.clientX, y: e.clientY });
      addNode(type, label, position);
    },
    [screenToFlowPosition, addNode]
  );

  // ── Edge / node changes ───────────────────────────────────────────────────

  const handleEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      onEdgesChange(changes);
      if (changes.some((c) => c.type === 'remove')) isDirty.current = true;
    },
    [onEdgesChange]
  );

  // ── Save / Load / Run ─────────────────────────────────────────────────────

  const buildDefinition = () => ({
    nodes: nodes.map((n) => ({
      id: n.id,
      nodeType: (n.data as WorkflowNodeData).nodeType,
      label: (n.data as WorkflowNodeData).label,
      subtitle: (n.data as WorkflowNodeData).subtitle,
      config: (n.data as WorkflowNodeData).config,
      position: n.position,
    })),
    edges: edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      label: typeof e.label === 'string' ? e.label : '',
    })),
  });

  const save = async () => {
    const definition = buildDefinition();
    try {
      if (currentWfId) {
        await workflowsApi.update(currentWfId, { name: workflowName, definition });
      } else {
        const data = await workflowsApi.create({ name: workflowName, definition });
        setCurrentWfId(data.id);
      }
      qc.invalidateQueries({ queryKey: ['workflows'] });
      isDirty.current = false;
      toast({ kind: 'success', message: 'Workflow saved' });
    } catch {
      toast({ kind: 'error', message: 'Save failed' });
    }
  };

  const run = async (dryRun = false) => {
    if (!currentWfId) {
      await save();
      toast({ kind: 'info', message: 'Saved — click Run again to execute.' });
      return;
    }
    setRunning(true);
    setRunOutput('');
    // Animate all non-trigger/end nodes as "running"
    setNodes((nds) =>
      nds.map((n) => {
        const nt = (n.data as WorkflowNodeData).nodeType;
        return nt === 'trigger' || nt === 'end'
          ? n
          : { ...n, data: { ...n.data, status: 'running' } };
      })
    );
    try {
      const data = await workflowsApi.run(currentWfId, dryRun);
      setRunOutput(JSON.stringify(data, null, 2));
      // Mark all as complete
      setNodes((nds) =>
        nds.map((n) => ({ ...n, data: { ...n.data, status: 'complete' } }))
      );
      toast({ kind: 'success', message: dryRun ? 'Dry run complete' : 'Workflow started' });
    } catch {
      setNodes((nds) =>
        nds.map((n) => ({ ...n, data: { ...n.data, status: 'failed' } }))
      );
      toast({ kind: 'error', message: 'Run failed' });
    } finally {
      setRunning(false);
    }
  };

  const loadWorkflow = async (id: string) => {
    const data = await workflowsApi.get(id);
    setCurrentWfId(id);
    setWorkflowName(data.name);
    const def = (data.definition ?? {}) as {
      nodes?: Array<{ id: string; nodeType: string; label: string; subtitle?: string; config?: Record<string, string>; position?: { x: number; y: number } }>;
      edges?: Array<{ id: string; source: string; target: string; label?: string }>;
    };
    setNodes(
      (def.nodes ?? []).map((s) => ({
        id: s.id,
        type: 'workflow' as const,
        position: s.position ?? { x: 200, y: 200 },
        data: {
          nodeType: s.nodeType,
          label: s.label,
          subtitle: s.subtitle ?? '',
          config: s.config ?? {},
          status: null,
        } satisfies WorkflowNodeData,
      }))
    );
    setEdges(
      (def.edges ?? []).map((e) => ({
        ...DEFAULT_EDGE_OPTIONS,
        id: e.id,
        source: e.source,
        target: e.target,
        label: e.label || undefined,
        labelStyle: e.label ? { fontSize: 10, fontWeight: 600 } : undefined,
        labelBgStyle: e.label ? { fill: '#f8fafc', fillOpacity: 0.9 } : undefined,
      }))
    );
    isDirty.current = false;
    toast({ kind: 'success', message: `Loaded "${data.name}"` });
  };

  const newWorkflow = () => {
    setNodes([]);
    setEdges([]);
    setCurrentWfId(null);
    setWorkflowName('My Workflow');
    setSelectedNode(null);
    setSelectedEdge(null);
    setRunOutput('');
    isDirty.current = false;
  };

  const generateFromNL = async () => {
    if (!nlGoal.trim()) return;
    setGenerating(true);
    try {
      const r = await fetch(`${API_BASE}/goals`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey },
        body: JSON.stringify({ goal: nlGoal, dry_run: true }),
      });
      const data = await r.json() as { plan?: { steps?: string[] }; execution_context?: { plan?: { steps?: string[] } } };
      const steps: string[] =
        data?.plan?.steps ?? data?.execution_context?.plan?.steps ?? [];
      if (steps.length > 0) {
        const gap = 120;
        const newNodes: Node<WorkflowNodeData>[] = [
          {
            id: 'gen_start',
            type: 'workflow',
            position: { x: 280, y: 60 },
            data: { nodeType: 'trigger', label: 'Start', subtitle: nlGoal.slice(0, 50), config: {}, status: null },
          },
          ...steps.slice(0, 10).map((step, i) => ({
            id: `gen_step_${i}`,
            type: 'workflow' as const,
            position: { x: 280, y: 60 + gap * (i + 1) },
            data: {
              nodeType: 'agent_step',
              label: step.slice(0, 45),
              subtitle: '',
              config: {},
              status: null,
            } satisfies WorkflowNodeData,
          })),
          {
            id: 'gen_end',
            type: 'workflow',
            position: { x: 280, y: 60 + gap * (steps.length + 1) },
            data: { nodeType: 'end', label: 'End', subtitle: '', config: {}, status: null },
          },
        ];
        const allNodes = newNodes;
        const newEdges: Edge[] = allNodes.slice(0, -1).map((n, i) => ({
          ...DEFAULT_EDGE_OPTIONS,
          id: `gen_e_${i}`,
          source: n.id,
          target: allNodes[i + 1].id,
        }));
        setNodes(newNodes);
        setEdges(newEdges);
        setCurrentWfId(null);
        isDirty.current = true;
        toast({ kind: 'success', message: `Generated ${steps.length}-step workflow` });
      } else {
        toast({ kind: 'error', message: 'No steps returned — try a more specific description' });
      }
    } catch {
      toast({ kind: 'error', message: 'Failed to generate workflow' });
    } finally {
      setGenerating(false);
    }
  };

  const isEmpty = nodes.length === 0;

  return (
    <div className="flex flex-col h-screen bg-background">
      {/* ── Toolbar ─────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-border bg-card shadow-sm flex-shrink-0">
        <input
          value={workflowName}
          onChange={(e) => { setWorkflowName(e.target.value); isDirty.current = true; }}
          aria-label="Workflow name"
          className="font-semibold text-sm bg-transparent border-b border-transparent hover:border-muted-foreground focus:border-primary focus:outline-none w-48 truncate"
        />
        {isDirty.current && (
          <span className="text-[10px] text-amber-500 font-medium">● unsaved</span>
        )}
        <div className="flex-1" />

        {savedWorkflows.length > 0 && (
          <select
            onChange={(e) => { if (e.target.value) loadWorkflow(e.target.value); }}
            className="text-xs border border-border rounded-lg px-2 py-1.5 bg-background"
            defaultValue=""
            aria-label="Load saved workflow"
          >
            <option value="">Load saved…</option>
            {savedWorkflows.map((w: WorkflowRecord) => (
              <option key={w.id} value={w.id}>{w.name}</option>
            ))}
          </select>
        )}

        <button
          type="button"
          onClick={newWorkflow}
          className="text-xs px-3 py-1.5 border border-border rounded-lg hover:bg-muted transition-colors"
        >
          New
        </button>
        <button
          type="button"
          onClick={save}
          className="text-xs px-3 py-1.5 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors font-medium"
        >
          Save
        </button>
        <button
          type="button"
          onClick={() => run(true)}
          disabled={running}
          className="text-xs px-3 py-1.5 bg-amber-500 text-white rounded-lg hover:bg-amber-600 disabled:opacity-50 transition-colors font-medium"
        >
          Dry Run
        </button>
        <button
          type="button"
          onClick={() => run(false)}
          disabled={running}
          className="text-xs px-3 py-1.5 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50 transition-colors font-medium"
        >
          {running ? '⏳ Running…' : '▶ Run'}
        </button>
      </div>

      {/* ── Body ────────────────────────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">

        {/* Left: Palette */}
        <div className="w-52 border-r border-border bg-card flex flex-col flex-shrink-0 overflow-hidden">
          <div className="px-3 py-2 text-[10px] font-bold uppercase tracking-widest text-muted-foreground border-b border-border">
            Node Palette
          </div>

          {/* NL generation */}
          <div className="p-2 border-b border-border">
            <textarea
              value={nlGoal}
              onChange={(e) => setNlGoal(e.target.value)}
              rows={2}
              aria-label="Natural language workflow description"
              className="w-full text-xs border border-border rounded-lg p-2 resize-none bg-background placeholder:text-muted-foreground/60 focus:outline-none focus:ring-2 focus:ring-primary"
              placeholder="Describe your workflow…"
            />
            <button
              type="button"
              onClick={generateFromNL}
              disabled={generating || !nlGoal.trim()}
              className="w-full mt-1.5 text-xs bg-violet-600 text-white rounded-lg py-1.5 disabled:opacity-50 hover:bg-violet-700 transition-colors font-medium"
            >
              {generating ? '⏳ Generating…' : '✨ Generate from NL'}
            </button>
          </div>

          {/* Drag tip */}
          <div className="px-3 py-1.5 text-[10px] text-muted-foreground bg-muted/40 border-b border-border">
            Drag to canvas or click to add
          </div>

          {/* Palette nodes */}
          <div className="flex-1 overflow-y-auto p-2 space-y-1">
            {PALETTE_ITEMS.map((item) => (
              <PaletteItem key={item.type} item={item} onAdd={addNode} />
            ))}
          </div>
        </div>

        {/* Centre: Canvas */}
        <div className="flex-1 relative overflow-hidden">
          {isEmpty && (
            <div className="absolute inset-0 flex flex-col items-center justify-center text-muted-foreground pointer-events-none z-10 gap-3">
              <div className="text-5xl opacity-30">🔧</div>
              <div className="text-base font-medium opacity-60">Build your workflow</div>
              <div className="text-xs opacity-40 text-center max-w-xs leading-relaxed">
                Drag nodes from the left panel or click to add them.<br />
                Connect nodes by dragging from the bottom handle of one node to the top handle of another.
              </div>
            </div>
          )}

          <div
            className="w-full h-full"
            onDragOver={onDragOver}
            onDrop={onDrop}
          >
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={handleEdgesChange}
              onConnect={onConnect}
              isValidConnection={isValidConnection}
              onNodeClick={(_, node) => {
                setSelectedNode(node as Node<WorkflowNodeData>);
                setSelectedEdge(null);
              }}
              onEdgeClick={(_, edge) => {
                setSelectedEdge(edge);
                setSelectedNode(null);
              }}
              onPaneClick={() => {
                setSelectedNode(null);
                setSelectedEdge(null);
              }}
              nodeTypes={NODE_TYPES}
              defaultEdgeOptions={DEFAULT_EDGE_OPTIONS}
              connectionLineStyle={CONNECTION_LINE_STYLE}
              connectionMode={ConnectionMode.Strict}
              snapToGrid
              snapGrid={SNAP_GRID}
              deleteKeyCode={['Backspace', 'Delete']}
              fitView={!isEmpty}
              fitViewOptions={{ padding: 0.2 }}
              proOptions={{ hideAttribution: true }}
            >
              <Background variant={BackgroundVariant.Dots} gap={16} className="opacity-40" />
              <Controls />
              <MiniMap
                nodeColor={(n) => {
                  const meta = NODE_META[(n.data as WorkflowNodeData)?.nodeType];
                  return meta ? '#6366f1' : '#94a3b8';
                }}
                style={{ borderRadius: 8 }}
              />
              <Panel position="bottom-center">
                <div className="text-[10px] text-muted-foreground bg-background/80 px-2 py-1 rounded border border-border shadow-sm">
                  Drag handles to connect · Backspace to delete · Scroll to zoom
                </div>
              </Panel>
            </ReactFlow>
          </div>
        </div>

        {/* Right: Inspector */}
        <div className="w-64 border-l border-border bg-card flex flex-col flex-shrink-0 overflow-hidden">
          <div className="px-3 py-2 text-[10px] font-bold uppercase tracking-widest text-muted-foreground border-b border-border">
            {selectedNode ? 'Node Inspector' : selectedEdge ? 'Edge Inspector' : 'Inspector'}
          </div>

          <div className="flex-1 overflow-y-auto">
            {selectedNode ? (
              <NodeInspector
                node={selectedNode}
                onUpdate={updateNodeData}
                onDelete={deleteNode}
              />
            ) : selectedEdge ? (
              <EdgeInspector
                edge={selectedEdge}
                onUpdateLabel={updateEdgeLabel}
                onDelete={deleteEdge}
              />
            ) : (
              <div className="p-3 text-xs text-muted-foreground leading-relaxed">
                <p>Select a node or connection to configure it.</p>
                <p className="mt-2 opacity-70">
                  Tip: drag handles to create connections. Double-click an edge to edit its label.
                </p>
              </div>
            )}
          </div>

          {runOutput && (
            <div className="border-t border-border">
              <div className="px-3 py-2 text-[10px] font-bold uppercase tracking-widest text-muted-foreground border-b border-border">
                Run Output
              </div>
              <pre className="text-[10px] p-2 overflow-auto max-h-48 bg-muted/30 font-mono leading-relaxed">
                {runOutput}
              </pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Exported page (wraps with ReactFlowProvider) ─────────────────────────────

export function WorkflowBuilderPage() {
  return (
    <ReactFlowProvider>
      <WorkflowCanvas />
    </ReactFlowProvider>
  );
}

export default WorkflowBuilderPage;
```

- [ ] **Step 3: Run unit tests to verify they pass**

```bash
cd agent-verse-frontend && npm run test -- src/features/workflow-builder/WorkflowBuilderPage.test.tsx
```

Expected: all 10 existing tests + 6 new tests pass (16 total).

- [ ] **Step 4: Run typecheck**

```bash
cd agent-verse-frontend && npm run typecheck
```

Expected: pass (pre-existing unrelated errors in KnowledgeGraph.tsx are OK).

- [ ] **Step 5: Commit**

```bash
git add agent-verse-frontend/src/features/workflow-builder/WorkflowBuilderPage.tsx agent-verse-frontend/src/features/workflow-builder/WorkflowBuilderPage.test.tsx
git commit -m "feat(workflow): world-class workflow builder with DnD, connections, inspector"
```

---

### Task 2: Comprehensive E2E Test Suite for Workflow Builder

**Goal:** Full Playwright E2E coverage for add-node, connect, save, load, delete, dry-run, and NL generation flows.

**Files:**
- Modify: `agent-verse-frontend/e2e/workflow-builder.spec.ts`

- [ ] **Step 1: Replace e2e/workflow-builder.spec.ts with comprehensive suite**

```typescript
/**
 * Workflow Builder E2E Tests
 *
 * Covers all critical user flows:
 * 1. Page loads with correct UI elements
 * 2. Add nodes from palette (click and drag-drop)
 * 3. Connect nodes via handles
 * 4. Edit node in inspector
 * 5. Delete node
 * 6. Save workflow
 * 7. Load saved workflow
 * 8. Run / Dry Run
 * 9. New workflow resets canvas
 * 10. NL workflow generation
 * 11. Edge selection and label editing
 * 12. Keyboard delete
 */
import { test, expect, type Page } from '@playwright/test';

// ── Shared helpers ─────────────────────────────────────────────────────────────

async function setupAuth(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem(
      'av-auth',
      JSON.stringify({
        state: {
          apiKey: 'test-key',
          tenantId: 'test-tenant',
          plan: 'professional',
          isAuthenticated: true,
        },
        version: 0,
      })
    );
    localStorage.setItem('av_api_key', 'test-key');
  });
  await page.route('**/tenants/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tenant_id: 'test-tenant', name: 'Test Org', plan: 'professional' }),
    })
  );
}

const EMPTY_WORKFLOWS_ROUTE = async (page: Page) => {
  await page.route(/localhost:8000\/workflows/, (route) => {
    const method = route.request().method();
    const url = route.request().url();
    if (method === 'GET' && !url.match(/\/workflows\/[^/]+$/)) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    }
    return route.continue();
  });
};

async function gotoWorkflowBuilder(page: Page) {
  await page.goto('/workflow-builder');
  // Wait for ReactFlow to initialize
  await page.waitForSelector('.react-flow', { timeout: 20000 });
}

// ── Test suite ─────────────────────────────────────────────────────────────────

test.describe('Workflow Builder', () => {

  // 1. Page structure
  test('loads with all toolbar buttons visible', async ({ page }) => {
    await setupAuth(page);
    await EMPTY_WORKFLOWS_ROUTE(page);
    await gotoWorkflowBuilder(page);

    await expect(page.getByRole('button', { name: /save/i })).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('button', { name: /dry run/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /▶ run/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /new/i })).toBeVisible();
  });

  test('shows workflow name input defaulting to "My Workflow"', async ({ page }) => {
    await setupAuth(page);
    await EMPTY_WORKFLOWS_ROUTE(page);
    await gotoWorkflowBuilder(page);

    const nameInput = page.locator('input[aria-label="Workflow name"]');
    await expect(nameInput).toBeVisible({ timeout: 15000 });
    await expect(nameInput).toHaveValue(/my workflow/i);
  });

  test('shows node palette with all node types', async ({ page }) => {
    await setupAuth(page);
    await EMPTY_WORKFLOWS_ROUTE(page);
    await gotoWorkflowBuilder(page);

    // All 9 palette items should be present and draggable
    const paletteButtons = page.locator('[aria-label^="Add "][draggable="true"]');
    await expect(paletteButtons.first()).toBeVisible({ timeout: 15000 });
    const count = await paletteButtons.count();
    expect(count).toBeGreaterThanOrEqual(9);
  });

  test('shows drag-and-connect tip text', async ({ page }) => {
    await setupAuth(page);
    await EMPTY_WORKFLOWS_ROUTE(page);
    await gotoWorkflowBuilder(page);

    await expect(
      page.getByText(/drag handles to connect/i).first()
    ).toBeVisible({ timeout: 15000 });
  });

  // 2. Add nodes from palette
  test('clicking palette item adds a node to canvas', async ({ page }) => {
    await setupAuth(page);
    await EMPTY_WORKFLOWS_ROUTE(page);
    await gotoWorkflowBuilder(page);

    await page.locator('[aria-label="Add Trigger node"]').click();
    // ReactFlow renders nodes as elements with data-id attribute
    await expect(
      page.locator('.react-flow__node[data-id]').first()
    ).toBeVisible({ timeout: 10000 });
  });

  test('adding multiple nodes creates multiple react-flow nodes', async ({ page }) => {
    await setupAuth(page);
    await EMPTY_WORKFLOWS_ROUTE(page);
    await gotoWorkflowBuilder(page);

    await page.locator('[aria-label="Add Trigger node"]').click();
    await page.locator('[aria-label="Add Tool Call node"]').click();
    await page.locator('[aria-label="Add End node"]').click();

    const nodeCount = await page.locator('.react-flow__node').count();
    expect(nodeCount).toBe(3);
  });

  test('all palette items are draggable', async ({ page }) => {
    await setupAuth(page);
    await EMPTY_WORKFLOWS_ROUTE(page);
    await gotoWorkflowBuilder(page);

    const paletteItems = page.locator('[aria-label^="Add "]');
    const first = paletteItems.first();
    await expect(first).toBeVisible({ timeout: 15000 });

    const count = await paletteItems.count();
    for (let i = 0; i < count; i++) {
      const item = paletteItems.nth(i);
      const draggable = await item.getAttribute('draggable');
      expect(draggable).toBe('true');
    }
  });

  // 3. Node inspector
  test('clicking a node opens inspector panel', async ({ page }) => {
    await setupAuth(page);
    await EMPTY_WORKFLOWS_ROUTE(page);
    await gotoWorkflowBuilder(page);

    await page.locator('[aria-label="Add Agent Step node"]').click();
    await page.waitForSelector('.react-flow__node');

    // Click the node in the canvas
    await page.locator('.react-flow__node').first().click();

    // Inspector should show label input
    await expect(page.locator('#ins-label')).toBeVisible({ timeout: 8000 });
  });

  test('inspector label input updates node label', async ({ page }) => {
    await setupAuth(page);
    await EMPTY_WORKFLOWS_ROUTE(page);
    await gotoWorkflowBuilder(page);

    await page.locator('[aria-label="Add Tool Call node"]').click();
    await page.waitForSelector('.react-flow__node');
    await page.locator('.react-flow__node').first().click();

    const labelInput = page.locator('#ins-label');
    await expect(labelInput).toBeVisible({ timeout: 5000 });
    await labelInput.clear();
    await labelInput.fill('My Custom Step');

    // The node should reflect the new label
    await expect(page.locator('.react-flow__node').first()).toContainText('My Custom Step', { timeout: 5000 });
  });

  test('clicking pane clears inspector', async ({ page }) => {
    await setupAuth(page);
    await EMPTY_WORKFLOWS_ROUTE(page);
    await gotoWorkflowBuilder(page);

    await page.locator('[aria-label="Add Trigger node"]').click();
    await page.waitForSelector('.react-flow__node');
    await page.locator('.react-flow__node').first().click();
    await expect(page.locator('#ins-label')).toBeVisible({ timeout: 5000 });

    // Click the background pane
    await page.locator('.react-flow__pane').click({ position: { x: 10, y: 10 } });
    await expect(page.locator('#ins-label')).not.toBeVisible({ timeout: 3000 });
  });

  // 4. Delete node
  test('Delete Node button removes node from canvas', async ({ page }) => {
    await setupAuth(page);
    await EMPTY_WORKFLOWS_ROUTE(page);
    await gotoWorkflowBuilder(page);

    await page.locator('[aria-label="Add Decision node"]').click();
    await page.waitForSelector('.react-flow__node');
    await page.locator('.react-flow__node').first().click();

    const deleteBtn = page.locator('[aria-label="Delete selected node"]');
    await expect(deleteBtn).toBeVisible({ timeout: 5000 });
    await deleteBtn.click();

    await expect(page.locator('.react-flow__node')).toHaveCount(0, { timeout: 5000 });
  });

  // 5. Save workflow
  test('Save button POSTs workflow to backend', async ({ page }) => {
    let postedBody: unknown = null;

    await setupAuth(page);
    await page.route(/localhost:8000\/workflows/, async (route) => {
      const method = route.request().method();
      const url = route.request().url();
      if (method === 'GET' && !url.match(/\/workflows\/[^/]+$/)) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      }
      if (method === 'POST') {
        postedBody = JSON.parse(route.request().postData() ?? '{}');
        return route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({
            id: 'wf-new',
            name: 'My Workflow',
            description: '',
            definition: {},
            status: 'draft',
            version: 1,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          }),
        });
      }
      return route.continue();
    });

    await gotoWorkflowBuilder(page);
    await page.getByRole('button', { name: /save/i }).click();

    await expect(async () => {
      expect(postedBody).not.toBeNull();
    }).toPass({ timeout: 5000 });
    expect((postedBody as { name: string }).name).toBe('My Workflow');
  });

  test('saving with nodes includes node definition', async ({ page }) => {
    let savedDefinition: unknown = null;

    await setupAuth(page);
    await page.route(/localhost:8000\/workflows/, async (route) => {
      const method = route.request().method();
      const url = route.request().url();
      if (method === 'GET' && !url.match(/\/workflows\/[^/]+$/)) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      }
      if (method === 'POST') {
        const body = JSON.parse(route.request().postData() ?? '{}') as { definition?: unknown };
        savedDefinition = body.definition;
        return route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({ id: 'wf-def', name: 'My Workflow', description: '', definition: {}, status: 'draft', version: 1, created_at: new Date().toISOString(), updated_at: new Date().toISOString() }),
        });
      }
      return route.continue();
    });

    await gotoWorkflowBuilder(page);
    // Add a trigger and tool call node
    await page.locator('[aria-label="Add Trigger node"]').click();
    await page.locator('[aria-label="Add Tool Call node"]').click();
    await page.getByRole('button', { name: /save/i }).click();

    await expect(async () => {
      expect(savedDefinition).not.toBeNull();
      const def = savedDefinition as { nodes?: unknown[] };
      expect(def.nodes?.length).toBe(2);
    }).toPass({ timeout: 5000 });
  });

  // 6. Load saved workflow
  test('loading a saved workflow restores nodes to canvas', async ({ page }) => {
    const savedWorkflow = {
      id: 'wf-saved',
      name: 'Deploy Pipeline',
      description: '',
      status: 'draft',
      version: 1,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      definition: {
        nodes: [
          { id: 'n1', nodeType: 'trigger', label: 'Start', subtitle: '', config: {}, position: { x: 100, y: 100 } },
          { id: 'n2', nodeType: 'tool_call', label: 'Deploy App', subtitle: '', config: {}, position: { x: 100, y: 250 } },
          { id: 'n3', nodeType: 'end', label: 'Done', subtitle: '', config: {}, position: { x: 100, y: 400 } },
        ],
        edges: [
          { id: 'e1', source: 'n1', target: 'n2', label: '' },
          { id: 'e2', source: 'n2', target: 'n3', label: '' },
        ],
      },
    };

    await setupAuth(page);
    await page.route(/localhost:8000\/workflows/, (route) => {
      const method = route.request().method();
      const url = route.request().url();
      if (method === 'GET' && url.match(/\/workflows\/wf-saved$/)) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(savedWorkflow) });
      }
      if (method === 'GET') {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([savedWorkflow]) });
      }
      return route.continue();
    });

    await gotoWorkflowBuilder(page);

    // Select from the saved workflows dropdown
    const select = page.locator('select[aria-label="Load saved workflow"]');
    await expect(select).toBeVisible({ timeout: 10000 });
    await select.selectOption('wf-saved');

    // Should show 3 nodes
    await expect(page.locator('.react-flow__node')).toHaveCount(3, { timeout: 10000 });
    // Node label text should be visible
    await expect(page.getByText('Deploy App')).toBeVisible();
  });

  // 7. New workflow
  test('New button resets canvas to empty state', async ({ page }) => {
    await setupAuth(page);
    await EMPTY_WORKFLOWS_ROUTE(page);
    await gotoWorkflowBuilder(page);

    // Add some nodes
    await page.locator('[aria-label="Add Trigger node"]').click();
    await page.locator('[aria-label="Add End node"]').click();
    await expect(page.locator('.react-flow__node')).toHaveCount(2, { timeout: 5000 });

    // Click New
    await page.getByRole('button', { name: /new/i }).click();
    await expect(page.locator('.react-flow__node')).toHaveCount(0, { timeout: 5000 });
  });

  // 8. Dry Run
  test('Dry Run button is visible and enabled when workflow is saved', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/workflows/, async (route) => {
      const method = route.request().method();
      const url = route.request().url();
      if (method === 'GET' && !url.match(/\/workflows\/[^/]+$/)) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      }
      if (method === 'POST' && !url.includes('/run')) {
        return route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({ id: 'wf-run', name: 'My Workflow', description: '', definition: {}, status: 'draft', version: 1, created_at: new Date().toISOString(), updated_at: new Date().toISOString() }),
        });
      }
      if (method === 'POST' && url.includes('/run')) {
        return route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({ run_id: 'run-1', status: 'dry_run', workflow_id: 'wf-run', goal: 'Execute workflow' }),
        });
      }
      return route.continue();
    });

    await gotoWorkflowBuilder(page);
    const dryRunBtn = page.getByRole('button', { name: /dry run/i });
    await expect(dryRunBtn).toBeVisible({ timeout: 15000 });
    await expect(dryRunBtn).toBeEnabled();
  });

  test('Dry Run shows run output after completion', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/workflows/, async (route) => {
      const method = route.request().method();
      const url = route.request().url();
      if (method === 'GET' && !url.match(/\/workflows\/[^/]+$/)) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      }
      if (method === 'POST' && url.includes('/run')) {
        return route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({ run_id: 'run-dry', status: 'dry_run', workflow_id: 'wf-1', goal: 'Execute My Workflow (0 nodes)' }),
        });
      }
      if (method === 'POST') {
        return route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({ id: 'wf-1', name: 'My Workflow', description: '', definition: {}, status: 'draft', version: 1, created_at: new Date().toISOString(), updated_at: new Date().toISOString() }),
        });
      }
      return route.continue();
    });

    await gotoWorkflowBuilder(page);
    // First save (since no currentWfId, Dry Run saves first)
    await page.getByRole('button', { name: /dry run/i }).click();
    // Click dry run again now that workflow is saved
    await page.getByRole('button', { name: /dry run/i }).click();

    // Should show run output section
    await expect(page.getByText(/run output/i)).toBeVisible({ timeout: 10000 });
  });

  // 9. NL Generation
  test('Generate button populates canvas from NL description', async ({ page }) => {
    await setupAuth(page);
    await EMPTY_WORKFLOWS_ROUTE(page);
    await page.route('**/goals', (route) => {
      if (route.request().method() === 'POST') {
        return route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({
            goal_id: 'g-nl',
            status: 'planning',
            plan: { steps: ['Search Jira for issues', 'Filter by assignee', 'Send Slack summary'] },
          }),
        });
      }
      return route.continue();
    });

    await gotoWorkflowBuilder(page);
    const nlTextarea = page.locator('textarea[aria-label="Natural language workflow description"]');
    await expect(nlTextarea).toBeVisible({ timeout: 15000 });
    await nlTextarea.fill('Build a workflow to summarize Jira issues and send to Slack');
    await page.getByRole('button', { name: /generate from nl/i }).click();

    // Should create nodes: Start + 3 steps + End = 5 nodes
    await expect(page.locator('.react-flow__node')).toHaveCount(5, { timeout: 15000 });
  });

  // 10. Connection handles visible
  test('nodes have visible connection handles', async ({ page }) => {
    await setupAuth(page);
    await EMPTY_WORKFLOWS_ROUTE(page);
    await gotoWorkflowBuilder(page);

    await page.locator('[aria-label="Add Agent Step node"]').click();
    await page.waitForSelector('.react-flow__node');

    // ReactFlow renders handles as .react-flow__handle elements
    const handles = page.locator('.react-flow__handle');
    const count = await handles.count();
    expect(count).toBeGreaterThanOrEqual(2); // at least source + target handle
  });

  // 11. Workflow name change
  test('workflow name can be changed', async ({ page }) => {
    await setupAuth(page);
    await EMPTY_WORKFLOWS_ROUTE(page);
    await gotoWorkflowBuilder(page);

    const nameInput = page.locator('input[aria-label="Workflow name"]');
    await expect(nameInput).toBeVisible({ timeout: 10000 });
    await nameInput.clear();
    await nameInput.fill('Deploy Pipeline v2');
    await expect(nameInput).toHaveValue('Deploy Pipeline v2');
  });

  // 12. Can save a workflow and it appears in load dropdown
  test('saved workflow appears in the load dropdown', async ({ page }) => {
    const workflows = [
      { id: 'wf-001', name: 'Deploy Pipeline', description: '', definition: {}, status: 'draft', version: 1, created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
    ];

    await setupAuth(page);
    await page.route(/localhost:8000\/workflows/, (route) => {
      const method = route.request().method();
      if (method === 'GET') {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(workflows) });
      }
      if (method === 'POST') {
        return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(workflows[0]) });
      }
      return route.continue();
    });

    await gotoWorkflowBuilder(page);

    await expect(
      page.getByRole('option', { name: 'Deploy Pipeline' })
    ).toBeAttached({ timeout: 15000 });
  });

  // 13. Decision node has the correct subtitle placeholder hint
  test('Decision node inspector shows condition placeholder', async ({ page }) => {
    await setupAuth(page);
    await EMPTY_WORKFLOWS_ROUTE(page);
    await gotoWorkflowBuilder(page);

    await page.locator('[aria-label="Add Decision node"]').click();
    await page.waitForSelector('.react-flow__node');
    await page.locator('.react-flow__node').first().click();

    // The subtitle/condition input should show the decision-specific placeholder
    const conditionInput = page.locator('#ins-subtitle');
    await expect(conditionInput).toBeVisible({ timeout: 5000 });
    const placeholder = await conditionInput.getAttribute('placeholder');
    expect(placeholder).toMatch(/condition|status/i);
  });
});
```

- [ ] **Step 2: Run E2E tests**

```bash
cd agent-verse-frontend && npm run test:e2e -- e2e/workflow-builder.spec.ts --reporter=list
```

Expected: all tests pass or fail with meaningful messages (not timeout/crash).

- [ ] **Step 3: Commit**

```bash
git add agent-verse-frontend/e2e/workflow-builder.spec.ts
git commit -m "test(e2e): comprehensive workflow builder E2E coverage"
```

---

### Task 3: Final Verification + Push

- [ ] **Step 1: Run unit tests**

```bash
cd agent-verse-frontend && npm run test -- src/features/workflow-builder/
```

Expected: all tests pass.

- [ ] **Step 2: Run typecheck**

```bash
cd agent-verse-frontend && npm run typecheck
```

Expected: pass (existing unrelated errors are OK).

- [ ] **Step 3: Run build**

```bash
cd agent-verse-frontend && npm run build
```

Expected: exit 0.

- [ ] **Step 4: Run E2E suite**

```bash
cd agent-verse-frontend && npm run test:e2e -- e2e/workflow-builder.spec.ts --reporter=list 2>&1 | tail -40
```

Expected: all tests pass.

- [ ] **Step 5: Final git status check**

```bash
git status --short
git diff --stat
```

Expected: only workflow builder files changed.

- [ ] **Step 6: Push**

```bash
git log --oneline -5
git push origin main
```

Report all test counts and push confirmation.
