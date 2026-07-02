/**
 * World-class visual workflow builder using @xyflow/react.
 * Drag-drop nodes, connect them, configure inline, save, run.
 */
import { useState, useCallback, useRef } from 'react';
import {
  ReactFlow, Background, Controls, MiniMap, BackgroundVariant,
  addEdge, useNodesState, useEdgesState, type Node, type Edge, type Connection,
  MarkerType, Handle, Position,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '../../stores/auth';
import { toast } from '../../stores/toast';
import { workflowsApi } from '../../lib/api/client';

// ─── Node Types ──────────────────────────────────────────────────────────────

const NODE_COLORS: Record<string, string> = {
  trigger:        'bg-green-100 border-green-400 text-green-800',
  tool_call:      'bg-blue-100 border-blue-400 text-blue-800',
  agent_step:     'bg-purple-100 border-purple-400 text-purple-800',
  decision:       'bg-yellow-100 border-yellow-400 text-yellow-800',
  parallel:       'bg-orange-100 border-orange-400 text-orange-800',
  loop:           'bg-cyan-100 border-cyan-400 text-cyan-800',
  human_approval: 'bg-red-100 border-red-400 text-red-800',
  delay:          'bg-slate-100 border-slate-400 text-slate-700',
  end:            'bg-muted/60 border-muted-foreground/50 text-gray-900 dark:text-gray-100',
};

const NODE_ICONS: Record<string, string> = {
  trigger: '▶', tool_call: '🔧', agent_step: '🤖', decision: '❓',
  parallel: '⫸', loop: '↻', human_approval: '👤', delay: '⏱', end: '⬛',
};

interface WorkflowNodeData {
  type: string;
  label: string;
  subtitle?: string;
  status?: string | null;
  [key: string]: unknown;
}

function WorkflowNode({ data, selected }: { data: WorkflowNodeData; selected?: boolean }) {
  const color = NODE_COLORS[data.type] ?? 'bg-gray-100 border-gray-300';
  return (
    <div
      className={`rounded-lg border-2 p-3 min-w-[140px] shadow-sm text-xs ${color} ${
        selected ? 'ring-2 ring-blue-500 ring-offset-1' : ''
      }`}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!bg-slate-400 !border-slate-600 !w-3 !h-3"
      />
      <div className="flex items-center gap-1.5 font-semibold mb-0.5">
        <span>{NODE_ICONS[data.type] ?? '◻'}</span>
        <span className="truncate">{String(data.label)}</span>
      </div>
      {data.subtitle && (
        <div className="text-[10px] opacity-60 truncate">{String(data.subtitle)}</div>
      )}
      {data.status && (
        <div
          className={`mt-1 text-[10px] font-medium ${
            data.status === 'running'  ? 'text-blue-600'  :
            data.status === 'complete' ? 'text-green-600' :
            data.status === 'failed'   ? 'text-red-600'   : 'opacity-50'
          }`}
        >
          ● {data.status}
        </div>
      )}
      <Handle
        type="source"
        position={Position.Bottom}
        className="!bg-slate-400 !border-slate-600 !w-3 !h-3"
      />
    </div>
  );
}

const NODE_TYPES = { workflow: WorkflowNode };
const SNAP_GRID: [number, number] = [16, 16]; // stable ref — prevents ReactFlow useEffect infinite loop

// ─── Palette ─────────────────────────────────────────────────────────────────

const PALETTE_NODES = [
  { type: 'trigger',        label: 'Trigger / Start'   },
  { type: 'tool_call',      label: 'Tool Call'          },
  { type: 'agent_step',     label: 'Agent Step'         },
  { type: 'decision',       label: 'Decision / Branch'  },
  { type: 'parallel',       label: 'Parallel Fan-out'   },
  { type: 'loop',           label: 'Loop / Map'         },
  { type: 'human_approval', label: 'Human Approval'     },
  { type: 'delay',          label: 'Delay / Wait'       },
  { type: 'end',            label: 'End'                },
];

// ─── Main Component ───────────────────────────────────────────────────────────

export function WorkflowBuilderPage() {
  const apiKey = useAuthStore((s) => s.apiKey); // primitive return avoids new-object-per-render re-render loop
  const qc = useQueryClient();
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [workflowName, setWorkflowName] = useState('My Workflow');
  const [currentWfId, setCurrentWfId] = useState<string | null>(null);
  const [nlGoal, setNlGoal] = useState('');
  const [generating, setGenerating] = useState(false);
  const [running, setRunning] = useState(false);
  const [runOutput, setRunOutput] = useState<string>('');
  const nodeCounter = useRef(1);

  const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

  const { data: savedWorkflows } = useQuery({
    queryKey: ['workflows'],
    queryFn: () => workflowsApi.list(),
    enabled: !!apiKey,
  });

  const addNode = useCallback((type: string, label: string) => {
    const id = `node_${nodeCounter.current++}`;
    setNodes((nds) => [
      ...nds,
      {
        id, type: 'workflow',
        position: { x: 200 + Math.random() * 200, y: 100 + Math.random() * 200 },
        data: { type, label, subtitle: '', status: null } satisfies WorkflowNodeData,
      },
    ]);
  }, [setNodes]);

  const onConnect = useCallback((connection: Connection) => {
    setEdges((eds) =>
      addEdge({
        ...connection,
        markerEnd: { type: MarkerType.ArrowClosed },
        style: { stroke: '#6366f1', strokeWidth: 2 },
      }, eds)
    );
  }, [setEdges]);

  const generateFromNL = async () => {
    if (!nlGoal.trim()) return;
    setGenerating(true);
    try {
      const r = await fetch(`${API_BASE}/goals`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey },
        body: JSON.stringify({ goal: nlGoal, dry_run: true }),
      });
      const data = await r.json();
      const steps: string[] = data?.plan?.steps ?? data?.execution_context?.plan?.steps ?? [];
      if (steps.length > 0) {
        const newNodes: Node[] = [
          { id: 'start', type: 'workflow', position: { x: 250, y: 50 }, data: { type: 'trigger', label: 'Start', subtitle: nlGoal.slice(0, 40) } satisfies WorkflowNodeData },
          ...steps.slice(0, 10).map((step: string, i: number) => ({
            id: `step_${i}`, type: 'workflow' as const,
            position: { x: 250, y: 150 + i * 100 },
            data: { type: 'tool_call', label: step.slice(0, 40), subtitle: '' } satisfies WorkflowNodeData,
          })),
          { id: 'end', type: 'workflow', position: { x: 250, y: 200 + steps.length * 100 }, data: { type: 'end', label: 'End' } satisfies WorkflowNodeData },
        ];
        const newEdges: Edge[] = newNodes.slice(0, -1).map((n, i) => ({
          id: `e_${i}`, source: n.id, target: newNodes[i + 1].id,
          markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 },
        }));
        setNodes(newNodes);
        setEdges(newEdges);
        toast({ kind: 'success', message: `Generated ${steps.length} steps from your goal` });
      } else {
        toast({ kind: 'error', message: 'No steps returned — try a more specific goal' });
      }
    } catch {
      toast({ kind: 'error', message: 'Failed to generate workflow' });
    } finally {
      setGenerating(false);
    }
  };

  const save = async () => {
    const definition = {
      steps: nodes.map((n) => ({
        id: n.id, type: (n.data as WorkflowNodeData).type,
        label: (n.data as WorkflowNodeData).label, position: n.position,
      })),
      edges: edges.map((e) => ({ source: e.source, target: e.target })),
    };
    try {
      if (currentWfId) {
        await workflowsApi.update(currentWfId, { name: workflowName, definition });
      } else {
        const data = await workflowsApi.create({ name: workflowName, definition });
        setCurrentWfId(data.id);
      }
      qc.invalidateQueries({ queryKey: ['workflows'] });
      toast({ kind: 'success', message: 'Workflow saved' });
    } catch {
      toast({ kind: 'error', message: 'Failed to save workflow' });
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
    try {
      const data = await workflowsApi.run(currentWfId, dryRun);
      setRunOutput(JSON.stringify(data, null, 2));
      if (dryRun) {
        setNodes((nds) => nds.map((n) => ({ ...n, data: { ...n.data, status: 'complete' } })));
      }
      toast({ kind: 'success', message: dryRun ? 'Dry run complete' : 'Workflow started' });
    } catch {
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
      steps?: Array<{ id: string; type: string; label: string; position?: { x: number; y: number } }>;
      edges?: Array<{ source: string; target: string }>;
    };
    if (def.steps) {
      setNodes(def.steps.map((s) => ({
        id: s.id, type: 'workflow' as const,
        position: s.position ?? { x: 200, y: 200 },
        data: { type: s.type, label: s.label, subtitle: '' } satisfies WorkflowNodeData,
      })));
      setEdges((def.edges ?? []).map((e, i) => ({
        id: `e_${i}`, source: e.source, target: e.target,
        markerEnd: { type: MarkerType.ArrowClosed },
        style: { stroke: '#6366f1', strokeWidth: 2 },
      })));
    }
  };

  const isEmpty = nodes.length === 0;

  return (
    <div className="flex flex-col h-screen bg-background">
      {/* Toolbar */}
      <div className="flex items-center gap-3 px-4 py-2 border-b bg-card">
        <input
          value={workflowName}
          onChange={(e) => setWorkflowName(e.target.value)}
          aria-label="Workflow name"
          className="font-semibold text-sm bg-transparent border-b border-transparent hover:border-muted-foreground focus:border-primary focus:outline-none w-48"
        />
        <div className="flex-1" />
        {savedWorkflows && savedWorkflows.length > 0 && (
          <select
            onChange={(e) => { if (e.target.value) loadWorkflow(e.target.value); }}
            className="text-xs border rounded px-2 py-1 bg-background"
            defaultValue=""
            aria-label="Load saved workflow"
          >
            <option value="">Load saved…</option>
            {savedWorkflows.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        )}
        <button onClick={() => { setNodes([]); setEdges([]); setCurrentWfId(null); setWorkflowName('My Workflow'); }} className="text-xs px-2 py-1 border rounded hover:bg-muted">New</button>
        <button onClick={save} className="text-xs px-3 py-1 bg-blue-600 text-white rounded hover:bg-blue-700">Save</button>
        <button onClick={() => run(true)} disabled={running} className="text-xs px-3 py-1 bg-yellow-500 text-white rounded hover:bg-yellow-600 disabled:opacity-50">Dry Run</button>
        <button onClick={() => run(false)} disabled={running} className="text-xs px-3 py-1 bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50">{running ? 'Running…' : '▶ Run'}</button>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Left: Node Palette */}
        <div className="w-48 border-r bg-card flex flex-col shrink-0">
          <div className="p-2 text-xs font-semibold text-muted-foreground border-b">Node Palette</div>
          <div className="p-2 border-b">
            <textarea
              value={nlGoal}
              onChange={(e) => setNlGoal(e.target.value)}
              rows={2}
              aria-label="Natural language workflow description"
              className="w-full text-xs border rounded p-1 resize-none bg-background"
              placeholder="Describe workflow…"
            />
            <button
              onClick={generateFromNL}
              disabled={generating || !nlGoal.trim()}
              className="w-full mt-1 text-xs bg-purple-600 text-white rounded py-1 disabled:opacity-50"
            >{generating ? '…' : '✨ Generate'}</button>
          </div>
          <div className="flex-1 overflow-y-auto p-2 space-y-1">
            {PALETTE_NODES.map((n) => (
              <button
                key={n.type}
                onClick={() => addNode(n.type, n.label)}
                aria-label={`Add ${n.label} node`}
                className={`w-full text-left text-xs p-2 rounded border ${NODE_COLORS[n.type] ?? ''} hover:opacity-90 transition-opacity`}
              >{NODE_ICONS[n.type]} {n.label}</button>
            ))}
          </div>
        </div>

        {/* Center: Canvas */}
        <div className="flex-1 relative">
          {isEmpty && (
            <div className="absolute inset-0 flex flex-col items-center justify-center text-muted-foreground pointer-events-none z-10">
              <div className="text-5xl mb-3">🔧</div>
              <div className="text-lg font-medium">Build your workflow</div>
              <div className="text-sm mt-1">Add nodes from the palette or generate from natural language</div>
            </div>
          )}
          <ReactFlow
            nodes={nodes} edges={edges}
            onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={(_, node) => setSelectedNode(node)}
            onPaneClick={() => setSelectedNode(null)}
            nodeTypes={NODE_TYPES}
            fitView snapToGrid snapGrid={SNAP_GRID}
            deleteKeyCode="Backspace"
          >
            <Background variant={BackgroundVariant.Dots} gap={16} />
            <Controls />
            <MiniMap style={{ background: '#f8fafc' }} />
          </ReactFlow>
        </div>

        {/* Right: Inspector */}
        <div className="w-64 border-l bg-card flex flex-col shrink-0">
          <div className="p-2 text-xs font-semibold text-muted-foreground border-b">
            {selectedNode ? 'Node Inspector' : 'Inspector'}
          </div>
          {selectedNode ? (
            <div className="p-3 space-y-3 text-xs">
              <div>
                <label htmlFor="node-label" className="text-muted-foreground block mb-1">Label</label>
                <input
                  id="node-label"
                  value={String((selectedNode.data as WorkflowNodeData).label ?? '')}
                  onChange={(e) => {
                    setNodes((nds) => nds.map((n) => n.id === selectedNode.id ? { ...n, data: { ...n.data, label: e.target.value } } : n));
                    setSelectedNode((prev) => prev ? { ...prev, data: { ...prev.data, label: e.target.value } } : null);
                  }}
                  className="w-full border rounded px-2 py-1 bg-background"
                />
              </div>
              <div>
                <label htmlFor="node-description" className="text-muted-foreground block mb-1">Description</label>
                <textarea
                  id="node-description"
                  value={String((selectedNode.data as WorkflowNodeData).subtitle ?? '')}
                  onChange={(e) => {
                    setNodes((nds) => nds.map((n) => n.id === selectedNode.id ? { ...n, data: { ...n.data, subtitle: e.target.value } } : n));
                    setSelectedNode((prev) => prev ? { ...prev, data: { ...prev.data, subtitle: e.target.value } } : null);
                  }}
                  rows={3} className="w-full border rounded px-2 py-1 resize-none bg-background"
                />
              </div>
              <div className="text-[10px] text-muted-foreground">
                Node ID: {selectedNode.id}<br />
                Type: {String((selectedNode.data as WorkflowNodeData).type)}
              </div>
              <button
                onClick={() => {
                  setNodes((nds) => nds.filter((n) => n.id !== selectedNode.id));
                  setEdges((eds) => eds.filter((e) => e.source !== selectedNode.id && e.target !== selectedNode.id));
                  setSelectedNode(null);
                }}
                aria-label="Delete selected node"
                className="w-full text-xs bg-red-50 text-red-600 border border-red-200 rounded py-1 hover:bg-red-100"
              >Delete Node</button>
            </div>
          ) : (
            <div className="p-3 text-xs text-muted-foreground">Click a node to inspect and configure it</div>
          )}
          {runOutput && (
            <div className="border-t p-2 overflow-auto">
              <div className="text-xs font-semibold mb-1">Run Output</div>
              <pre className="text-[10px] bg-muted rounded p-2 overflow-auto max-h-40">{runOutput}</pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default WorkflowBuilderPage;
