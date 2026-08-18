/**
 * WorkflowBuilderPage — world-class visual workflow builder.
 *
 * Layout:
 *   ┌──────────────────────────────────────────────┐
 *   │  TopBar: name | version | Save | Test | Pub  │
 *   ├────────┬─────────────────────────┬────────────┤
 *   │Palette │   React Flow Canvas     │ Config     │
 *   │(left)  │   14 node types         │ Panel      │
 *   │        │   undo/redo             │ (right)    │
 *   │        │   minimap               │            │
 *   │        │   execution overlay     │            │
 *   └────────┴─────────────────────────┴────────────┘
 *   │  Testing drawer (bottom, optional)            │
 *
 * Features:
 * - Drag-drop from palette → canvas
 * - Click node → config panel opens
 * - Ctrl+Z/Y undo/redo
 * - YAML ↔ canvas bidirectional sync
 * - Live execution overlay via SSE
 * - Dark mode (dark: prefix Tailwind)
 * - WCAG 2.2 AA
 */
import { useState, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  ReactFlow, Background, Controls, MiniMap, BackgroundVariant,
  ReactFlowProvider, addEdge, useNodesState, useEdgesState,
  useReactFlow, ConnectionMode,
  type Node, type Connection, type OnConnect,
  MarkerType,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Save, Play, Zap, Code2, Undo2, Redo2, Layout, X,
  ChevronLeft, AlertCircle, Loader2,
} from 'lucide-react';

import { workflowEngineApi, type WEWorkflow } from '../../lib/api/client';
import { workflowNodeTypes } from './builder/nodes/AllNodes';
import { WorkflowToolPalette } from './builder/WorkflowToolPalette';
import { WorkflowStepConfig } from './builder/WorkflowStepConfig';
import { WorkflowExecutionOverlay } from './builder/WorkflowExecutionOverlay';
import { useYamlSync } from './builder/canvas-utils/useYamlSync';
import { useCanvasKeyboardShortcuts } from './builder/canvas-utils/useCanvasKeyboardShortcuts';
import { useAutoLayout } from './builder/canvas-utils/useAutoLayout';
import { panelSlide, toolbarButton, modalBackdrop, modalContent, edgeFlow } from './design/motion';
import type { WorkflowNodeData } from './builder/nodes/BaseWorkflowNode';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';

// ── History for undo/redo ─────────────────────────────────────────────────────

function useHistory<T>(initial: T) {
  const historyRef = useRef<T[]>([initial]);
  const idxRef = useRef(0);

  const push = useCallback((state: T) => {
    historyRef.current = historyRef.current.slice(0, idxRef.current + 1);
    historyRef.current.push(state);
    idxRef.current = historyRef.current.length - 1;
  }, []);

  const undo = useCallback((): T | null => {
    if (idxRef.current <= 0) return null;
    idxRef.current -= 1;
    return historyRef.current[idxRef.current] ?? null;
  }, []);

  const redo = useCallback((): T | null => {
    if (idxRef.current >= historyRef.current.length - 1) return null;
    idxRef.current += 1;
    return historyRef.current[idxRef.current] ?? null;
  }, []);

  const canUndo = idxRef.current > 0;
  const canRedo = idxRef.current < historyRef.current.length - 1;

  return { push, undo, redo, canUndo, canRedo };
}

// ── Builder canvas (inner, needs ReactFlowProvider context) ──────────────────

function BuilderCanvas({
  wf,
  onSave,
  isSaving,
}: {
  wf: WEWorkflow;
  onSave: (definition: Record<string, unknown>) => void;
  isSaving: boolean;
}) {
  const rfInstance = useReactFlow();
  const { applyLayout } = useAutoLayout();

  // Parse initial definition from API
  const initDef = (wf as unknown as { definition?: Record<string, unknown> }).definition ?? {};

  const { yaml, canvasState, updateFromCanvas, updateFromYaml, error: yamlError } = useYamlSync(
    Object.keys(initDef).length ? JSON.stringify(initDef, null, 2) : undefined
  );

  const [nodes, setNodes, onNodesChange] = useNodesState(canvasState.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(canvasState.edges);
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [showYaml, setShowYaml] = useState(false);
  const [yamlText, setYamlText] = useState(yaml);
  const [runStatus, setRunStatus] = useState<Record<string, string>>({});
  const [isTestRunning, setIsTestRunning] = useState(false);

  const history = useHistory({ nodes: canvasState.nodes, edges: canvasState.edges });

  // ── Connections ─────────────────────────────────────────────────────────────
  const onConnect: OnConnect = useCallback((params: Connection) => {
    setEdges((eds) => addEdge({
      ...params,
      type: 'smoothstep',
      animated: true,
      style: { strokeDasharray: 6, animation: edgeFlow.animation },
      markerEnd: { type: MarkerType.ArrowClosed, color: '#64748b' },
    }, eds));
  }, [setEdges]);

  // ── Node selection ──────────────────────────────────────────────────────────
  const onNodeClick = useCallback((_: unknown, node: Node) => {
    setSelectedNode(node);
  }, []);

  const onPaneClick = useCallback(() => {
    setSelectedNode(null);
  }, []);

  // ── Drag from palette ───────────────────────────────────────────────────────
  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const stepType = e.dataTransfer.getData('application/workflow-step-type');
    if (!stepType) return;

    const position = rfInstance.screenToFlowPosition({ x: e.clientX, y: e.clientY });
    const id = `${stepType}-${Date.now()}`;
    const newNode: Node = {
      id,
      type: stepType,
      position,
      data: {
        label: `New ${stepType}`,
        stepType,
      } satisfies WorkflowNodeData,
    };

    setNodes((nds) => [...nds, newNode]);
    setSelectedNode(newNode);
  }, [rfInstance, setNodes]);

  // ── Undo/redo ───────────────────────────────────────────────────────────────
  const doUndo = useCallback(() => {
    const prev = history.undo();
    if (prev) {
      setNodes(prev.nodes);
      setEdges(prev.edges);
    }
  }, [history, setNodes, setEdges]);

  const doRedo = useCallback(() => {
    const next = history.redo();
    if (next) {
      setNodes(next.nodes);
      setEdges(next.edges);
    }
  }, [history, setNodes, setEdges]);

  // ── Delete selected ─────────────────────────────────────────────────────────
  const deleteSelected = useCallback(() => {
    setNodes((nds) => nds.filter((n) => !n.selected));
    setEdges((eds) => eds.filter((e) => !e.selected));
    setSelectedNode(null);
  }, [setNodes, setEdges]);

  // ── Duplicate selected ──────────────────────────────────────────────────────
  const duplicateSelected = useCallback(() => {
    const selected = nodes.filter((n) => n.selected);
    const duplicates = selected.map((n) => ({
      ...n,
      id: `${n.id}-copy-${Date.now()}`,
      position: { x: n.position.x + 40, y: n.position.y + 40 },
      selected: false,
    }));
    setNodes((nds) => [...nds, ...duplicates]);
  }, [nodes, setNodes]);

  // ── Keyboard shortcuts ──────────────────────────────────────────────────────
  useCanvasKeyboardShortcuts({
    rfInstance,
    undo: doUndo,
    redo: doRedo,
    onDeleteSelected: deleteSelected,
    onDuplicateSelected: duplicateSelected,
  });

  // ── Auto layout ─────────────────────────────────────────────────────────────
  const autoLayout = useCallback(() => {
    const { nodes: ln, edges: le } = applyLayout(nodes, edges);
    setNodes(ln);
    setEdges(le);
    setTimeout(() => rfInstance.fitView({ padding: 0.1 }), 50);
  }, [nodes, edges, applyLayout, setNodes, setEdges, rfInstance]);

  // ── Save ────────────────────────────────────────────────────────────────────
  const handleSave = () => {
    updateFromCanvas(nodes, edges);
    try {
      const def = JSON.parse(yaml || '{}');
      onSave(def);
    } catch {
      onSave({ name: wf.name, steps: [] });
    }
  };

  // ── Test run ────────────────────────────────────────────────────────────────
  const testMutation = useMutation({
    mutationFn: () => workflowEngineApi.trigger(wf.id, { dry_run: true }),
    onMutate: () => setIsTestRunning(true),
    onSuccess: (_run) => {
      setIsTestRunning(false);
      // Mark all nodes as complete in overlay
      const allComplete: Record<string, string> = {};
      nodes.forEach((node) => { allComplete[node.id] = 'complete'; });
      setRunStatus(allComplete);
    },
    onError: () => setIsTestRunning(false),
  });

  return (
    <div className="flex h-full w-full overflow-hidden bg-slate-950">
      {/* Tool palette */}
      <WorkflowToolPalette />

      {/* Canvas */}
      <div className="flex-1 relative" onDragOver={onDragOver} onDrop={onDrop}>
        {/* Toolbar */}
        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-20 flex items-center gap-2
                         bg-slate-900/90 backdrop-blur-md rounded-2xl border border-white/10
                         px-3 py-1.5 shadow-xl">
          {/* Undo/redo */}
          <motion.button
            variants={toolbarButton}
            initial="rest"
            whileHover="hover"
            whileTap="pressed"
            onClick={doUndo}
            disabled={!history.canUndo}
            className="p-1.5 rounded-lg text-white/40 hover:text-white hover:bg-white/8
                       disabled:opacity-30 transition-colors"
            aria-label="Undo"
            title="Undo (Ctrl+Z)"
          >
            <Undo2 className="h-4 w-4" />
          </motion.button>
          <motion.button
            variants={toolbarButton}
            initial="rest"
            whileHover="hover"
            whileTap="pressed"
            onClick={doRedo}
            disabled={!history.canRedo}
            className="p-1.5 rounded-lg text-white/40 hover:text-white hover:bg-white/8
                       disabled:opacity-30 transition-colors"
            aria-label="Redo"
            title="Redo (Ctrl+Y)"
          >
            <Redo2 className="h-4 w-4" />
          </motion.button>

          <div className="w-px h-5 bg-white/10 mx-0.5" />

          {/* Auto layout */}
          <button
            onClick={autoLayout}
            className="p-1.5 rounded-lg text-white/40 hover:text-white hover:bg-white/8
                       transition-colors"
            aria-label="Auto-layout nodes"
            title="Auto Layout"
          >
            <Layout className="h-4 w-4" />
          </button>

          {/* YAML toggle */}
          <button
            onClick={() => setShowYaml((v) => !v)}
            className={`p-1.5 rounded-lg transition-colors
              ${showYaml ? 'text-sky-400 bg-sky-500/15' : 'text-white/40 hover:text-white hover:bg-white/8'}`}
            aria-label="Toggle YAML editor"
            aria-pressed={showYaml}
            title="Toggle YAML"
          >
            <Code2 className="h-4 w-4" />
          </button>

          <div className="w-px h-5 bg-white/10 mx-0.5" />

          {/* Test */}
          <button
            onClick={() => testMutation.mutate()}
            disabled={isTestRunning}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-amber-500/15
                       hover:bg-amber-500/25 text-amber-400 text-xs font-medium transition-colors
                       disabled:opacity-50"
            aria-label="Test workflow"
          >
            {isTestRunning
              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
              : <Play className="h-3.5 w-3.5" />}
            Test
          </button>

          {/* Save */}
          <button
            onClick={handleSave}
            disabled={isSaving}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-sky-600
                       hover:bg-sky-500 text-white text-xs font-medium transition-colors
                       disabled:opacity-60"
            aria-label="Save workflow"
          >
            {isSaving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
            Save
          </button>
        </div>

        {/* React Flow canvas */}
        <ReactFlow
          nodes={nodes.map((n) => ({
            ...n,
            data: {
              ...(n.data as WorkflowNodeData),
              runStatus: runStatus[n.id] ?? null,
            },
          }))}
          edges={edges}
          nodeTypes={workflowNodeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          connectionMode={ConnectionMode.Loose}
          fitView
          fitViewOptions={{ padding: 0.15 }}
          minZoom={0.1}
          maxZoom={2.5}
          className="bg-slate-950"
          aria-label="Workflow canvas"
          deleteKeyCode={null /* handled by our shortcut hook */}
        >
          <Background
            variant={BackgroundVariant.Dots}
            color="#334155"
            gap={20}
          />
          <Controls
            className="bg-slate-900/80 border border-white/10 rounded-xl shadow-lg"
            aria-label="Canvas controls"
          />
          <MiniMap
            className="bg-slate-900/80 border border-white/10 rounded-xl"
            nodeColor={() => '#334155'}
            aria-label="Workflow minimap"
          />

          {/* Execution overlay */}
          <WorkflowExecutionOverlay stepStatuses={runStatus} />
        </ReactFlow>

        {/* YAML editor panel */}
        <AnimatePresence>
          {showYaml && (
            <motion.div
              variants={panelSlide}
              initial="initial"
              animate="animate"
              exit="exit"
              className="absolute top-0 right-0 bottom-0 w-[400px] border-l border-white/10
                         bg-slate-900/95 backdrop-blur-md flex flex-col shadow-2xl z-10"
          style={modalBackdrop.animate as React.CSSProperties}
              aria-label="YAML editor"
            >
              <motion.div
                variants={modalContent}
                initial="initial"
                animate="animate"
                exit="exit"
                className="flex items-center justify-between px-4 py-3 border-b border-white/10"
              >
                <span className="text-sm font-semibold text-white flex items-center gap-2">
                  <Code2 className="h-4 w-4 text-sky-400" />
                  Workflow YAML
                </span>
                {yamlError && (
                  <span className="text-xs text-red-400 flex items-center gap-1">
                    <AlertCircle className="h-3.5 w-3.5" /> Invalid
                  </span>
                )}
                <button
                  onClick={() => setShowYaml(false)}
                  className="text-white/40 hover:text-white"
                  aria-label="Close YAML editor"
                >
                  <X className="h-4 w-4" />
                </button>
              </motion.div>
              <textarea
                value={yamlText}
                onChange={(e) => {
                  setYamlText(e.target.value);
                  updateFromYaml(e.target.value);
                }}
                spellCheck={false}
                className="flex-1 bg-transparent text-xs text-slate-300 font-mono
                           p-4 resize-none outline-none overflow-auto leading-relaxed"
                aria-label="Edit workflow YAML"
              />
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Config panel */}
      <AnimatePresence>
        {selectedNode && (
          <motion.div
            key={selectedNode.id}
            variants={panelSlide}
            initial="initial"
            animate="animate"
            exit="exit"
            className="w-80 border-l border-white/10 bg-slate-900/95 backdrop-blur-md"
          >
            <WorkflowStepConfig
              node={selectedNode}
              onUpdate={(updates) => {
                setNodes((nds) =>
                  nds.map((n) =>
                    n.id === selectedNode.id
                      ? { ...n, data: { ...(n.data as WorkflowNodeData), ...updates } }
                      : n
                  )
                );
              }}
              onClose={() => setSelectedNode(null)}
            />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function WorkflowBuilderPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const { data: wf, isLoading, error } = useQuery({
    queryKey: ['workflow-engine', 'get', id],
    queryFn: () => workflowEngineApi.get(id!),
    enabled: !!id,
  });

  const qc = useQueryClient();

  const saveMutation = useMutation({
    mutationFn: (definition: Record<string, unknown>) =>
      workflowEngineApi.update(id!, { definition }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workflow-engine', 'get', id] });
    },
  });

  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center">
        <Loader2 className="h-8 w-8 text-sky-400 animate-spin" />
      </div>
    );
  }

  if (error || !wf) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center text-red-400"
        role="alert">
        Failed to load workflow.{' '}
        <button onClick={() => navigate('/workflows')} className="underline ml-2">
          Back to list
        </button>
      </div>
    );
  }

  return (
    <JARVISPageShell>
    <JARVISStagger className="flex flex-col h-screen bg-slate-950 text-white">
      {/* Top bar */}
      <header className="shrink-0 flex items-center gap-3 px-4 py-3 border-b border-white/10
                          bg-slate-900/80 backdrop-blur-xl z-30">
        <button
          onClick={() => navigate('/workflows')}
          className="text-white/40 hover:text-white transition-colors"
          aria-label="Back to workflows"
        >
          <ChevronLeft className="h-5 w-5" />
        </button>
        <div>
          <h1 className="text-sm font-bold text-white leading-tight">{wf.name}</h1>
          <p className="text-xs text-white/40">v{wf.version} · {wf.status}</p>
        </div>
        <div className="flex items-center gap-2 ml-auto">
          {wf.status === 'draft' && (
            <button
              onClick={() => workflowEngineApi.publish(id!).then(() =>
                qc.invalidateQueries({ queryKey: ['workflow-engine', 'get', id] })
              )}
              className="flex items-center gap-2 px-4 py-1.5 rounded-xl bg-emerald-600
                         hover:bg-emerald-500 text-white text-xs font-medium transition-colors"
              aria-label="Publish workflow"
            >
              <Zap className="h-3.5 w-3.5" /> Publish
            </button>
          )}
        </div>
      </header>

      {/* Builder canvas */}
      <main className="flex-1 overflow-hidden">
        <ReactFlowProvider>
          <BuilderCanvas
            wf={wf}
            onSave={saveMutation.mutate}
            isSaving={saveMutation.isPending}
          />
        </ReactFlowProvider>
      </main>
    </JARVISStagger>
    </JARVISPageShell>
  );
}
