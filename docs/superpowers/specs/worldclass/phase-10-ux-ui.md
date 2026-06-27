# Phase 10: UX/UI (Workflow Builder, Playground, Push Notifications, Token Streaming, Mobile)

**Status:** Not started  
**Priority:** Medium-High — directly impacts developer and end-user adoption  
**Acceptance gate:** `vitest run` green; `agentverse dev` serves all new routes; Workflow Builder renders a graph from a dry-run plan; Playground steps through goal execution; browser push notification fires on goal completion; live tokens appear in GoalDetailPage; sidebar collapses to bottom tab bar at 375px viewport.

---

## 1. Current State

| Area | File | Current Behaviour |
|------|------|-------------------|
| Workflow builder | `agent-verse-frontend/src/features/` | No `workflow-builder/` directory. No visual node editor. |
| Playground | `agent-verse-frontend/src/features/` | No `playground/` directory. Only the full GoalDetailPage for post-run inspection. |
| Push notifications | `agent-verse-frontend/src/services/` | No notification service; no in-app bell; no browser push. |
| Token streaming | `agent-verse-frontend/src/features/goals/GoalDetailPage.tsx` | Shows completed step output; no live typing effect. |
| Mobile layout | All feature pages | Fixed-width sidebar visible at all breakpoints; cards do not reflow for small screens. |
| Frontend routing | `agent-verse-frontend/src/App.tsx` | No routes for `/workflow-builder` or `/playground`. |

---

## 2. Gap Description

The frontend lacks interactive authoring tools: developers cannot visually design agent plans or step through execution one step at a time. There is no notification system to alert users of completions without polling. Live token streaming (already supported by the backend) is not wired to the UI. The layout breaks on mobile viewports below 768px.

---

## 3. Full Implementation

### 3.1 Visual Workflow Builder

#### `src/features/workflow-builder/WorkflowBuilderPage.tsx`

```tsx
/**
 * WorkflowBuilderPage — visual drag-and-drop agent workflow editor.
 *
 * Uses @xyflow/react (ReactFlow) for the node graph.
 * Nodes:
 *   - StartNode: entry point (goal text input)
 *   - StepNode:  a single tool call (tool selector + args editor)
 *   - DecisionNode: conditional branch (condition + true/false edges)
 *   - EndNode:   workflow termination
 *
 * "Generate from Goal" → POST /goals?dry_run=true → parse plan → build graph
 * "Run Workflow"       → POST /goals with structured plan
 * "Export"            → download as YAML/JSON agent manifest
 */

import React, { useCallback, useState } from "react";
import {
  ReactFlow,
  Controls,
  MiniMap,
  Background,
  addEdge,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type Connection,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { StartNode } from "./nodes/StartNode";
import { StepNode } from "./nodes/StepNode";
import { DecisionNode } from "./nodes/DecisionNode";
import { EndNode } from "./nodes/EndNode";
import { WorkflowToolbar } from "./WorkflowToolbar";
import { useWorkflowActions } from "./hooks/useWorkflowActions";

const nodeTypes = {
  start: StartNode,
  step: StepNode,
  decision: DecisionNode,
  end: EndNode,
};

const initialNodes: Node[] = [
  {
    id: "start-1",
    type: "start",
    position: { x: 250, y: 50 },
    data: { label: "Start", goal: "" },
  },
  {
    id: "end-1",
    type: "end",
    position: { x: 250, y: 400 },
    data: { label: "End" },
  },
];

const initialEdges: Edge[] = [];

export function WorkflowBuilderPage() {
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);
  const [goalText, setGoalText] = useState("");
  const [isGenerating, setIsGenerating] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [lastGoalId, setLastGoalId] = useState<string | null>(null);

  const { generateFromGoal, runWorkflow, exportYAML, exportJSON } =
    useWorkflowActions({ nodes, edges, setNodes, setEdges, goalText });

  const onConnect = useCallback(
    (params: Connection) => setEdges((eds) => addEdge(params, eds)),
    [setEdges],
  );

  const addStepNode = useCallback(() => {
    const id = `step-${Date.now()}`;
    const newNode: Node = {
      id,
      type: "step",
      position: { x: 250, y: 200 + nodes.length * 80 },
      data: { label: "New Step", toolName: "", args: "{}" },
    };
    setNodes((nds) => [...nds, newNode]);
  }, [nodes.length, setNodes]);

  const addDecisionNode = useCallback(() => {
    const id = `decision-${Date.now()}`;
    const newNode: Node = {
      id,
      type: "decision",
      position: { x: 250, y: 200 + nodes.length * 80 },
      data: { label: "Decision", condition: "" },
    };
    setNodes((nds) => [...nds, newNode]);
  }, [nodes.length, setNodes]);

  const handleGenerateFromGoal = async () => {
    if (!goalText.trim()) return;
    setIsGenerating(true);
    try {
      await generateFromGoal();
    } finally {
      setIsGenerating(false);
    }
  };

  const handleRunWorkflow = async () => {
    setIsRunning(true);
    try {
      const goalId = await runWorkflow();
      setLastGoalId(goalId);
    } finally {
      setIsRunning(false);
    }
  };

  return (
    <div className="flex flex-col h-screen bg-gray-950">
      {/* Top bar */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-800 bg-gray-900">
        <h1 className="text-white font-semibold text-lg">Workflow Builder</h1>
        <div className="flex-1 flex gap-2 max-w-xl">
          <input
            type="text"
            value={goalText}
            onChange={(e) => setGoalText(e.target.value)}
            placeholder="Describe your goal to auto-generate a workflow..."
            className="flex-1 rounded-md bg-gray-800 border border-gray-700 text-white text-sm px-3 py-1.5 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            onKeyDown={(e) => e.key === "Enter" && handleGenerateFromGoal()}
          />
          <button
            onClick={handleGenerateFromGoal}
            disabled={isGenerating || !goalText.trim()}
            className="px-3 py-1.5 rounded-md bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {isGenerating ? "Generating…" : "Generate"}
          </button>
        </div>

        <WorkflowToolbar
          onAddStep={addStepNode}
          onAddDecision={addDecisionNode}
          onExportYAML={exportYAML}
          onExportJSON={exportJSON}
          onRun={handleRunWorkflow}
          isRunning={isRunning}
        />
      </div>

      {/* Canvas */}
      <div className="flex-1 relative">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          nodeTypes={nodeTypes}
          fitView
          proOptions={{ hideAttribution: true }}
        >
          <Controls />
          <MiniMap
            nodeColor={(node) => {
              switch (node.type) {
                case "start": return "#10b981";
                case "step": return "#6366f1";
                case "decision": return "#f59e0b";
                case "end": return "#ef4444";
                default: return "#374151";
              }
            }}
          />
          <Background color="#374151" gap={16} />
        </ReactFlow>
      </div>

      {/* Status bar */}
      {lastGoalId && (
        <div className="px-4 py-2 bg-green-900/30 border-t border-green-800 text-green-400 text-sm">
          Workflow running — Goal ID:{" "}
          <a
            href={`/goals/${lastGoalId}`}
            className="underline hover:text-green-300"
          >
            {lastGoalId}
          </a>
        </div>
      )}
    </div>
  );
}
```

#### `src/features/workflow-builder/nodes/StepNode.tsx`

```tsx
import React, { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";

export const StepNode = memo(({ data, selected }: NodeProps) => {
  return (
    <div
      className={`
        min-w-[200px] rounded-lg border-2 bg-gray-800 p-3 shadow-lg transition-all
        ${selected ? "border-indigo-400 shadow-indigo-900/50" : "border-gray-600"}
      `}
    >
      <Handle type="target" position={Position.Top} className="!bg-indigo-500" />

      <div className="flex items-center gap-2 mb-2">
        <div className="w-2 h-2 rounded-full bg-indigo-500" />
        <span className="text-indigo-300 text-xs font-medium uppercase tracking-wide">Step</span>
      </div>

      <div className="text-white text-sm font-medium mb-1">
        {String(data.label || "Unnamed Step")}
      </div>

      {data.toolName && (
        <div className="text-gray-400 text-xs font-mono bg-gray-700 rounded px-2 py-0.5 mt-1">
          🔧 {String(data.toolName)}
        </div>
      )}

      <Handle type="source" position={Position.Bottom} className="!bg-indigo-500" />
    </div>
  );
});

StepNode.displayName = "StepNode";
```

#### `src/features/workflow-builder/nodes/DecisionNode.tsx`

```tsx
import React, { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";

export const DecisionNode = memo(({ data, selected }: NodeProps) => {
  return (
    <div className="relative">
      <Handle type="target" position={Position.Top} className="!bg-amber-500" />

      {/* Diamond shape via CSS */}
      <div
        className={`
          w-28 h-28 flex items-center justify-center
          bg-gray-800 border-2 rotate-45 shadow-lg transition-all
          ${selected ? "border-amber-400" : "border-gray-600"}
        `}
      >
        <span className="-rotate-45 text-amber-300 text-xs font-medium text-center px-2">
          {String(data.condition || "Condition")}
        </span>
      </div>

      {/* True branch */}
      <Handle
        type="source"
        position={Position.Right}
        id="true"
        className="!bg-green-500"
        style={{ top: "50%" }}
      />

      {/* False branch */}
      <Handle
        type="source"
        position={Position.Bottom}
        id="false"
        className="!bg-red-500"
      />
    </div>
  );
});

DecisionNode.displayName = "DecisionNode";
```

#### `src/features/workflow-builder/nodes/StartNode.tsx`

```tsx
import React, { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";

export const StartNode = memo(({ data }: NodeProps) => (
  <div className="min-w-[180px] rounded-full bg-emerald-900/50 border-2 border-emerald-500 px-6 py-3 text-center shadow-lg">
    <div className="text-emerald-400 text-xs font-medium uppercase tracking-wide mb-1">Start</div>
    <div className="text-white text-sm">{String(data.goal || "Goal")}</div>
    <Handle type="source" position={Position.Bottom} className="!bg-emerald-500" />
  </div>
));

StartNode.displayName = "StartNode";
```

#### `src/features/workflow-builder/nodes/EndNode.tsx`

```tsx
import React, { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";

export const EndNode = memo(() => (
  <div className="min-w-[120px] rounded-full bg-red-900/30 border-2 border-red-500 px-6 py-3 text-center shadow-lg">
    <Handle type="target" position={Position.Top} className="!bg-red-500" />
    <div className="text-red-400 text-xs font-medium uppercase tracking-wide">End</div>
  </div>
));

EndNode.displayName = "EndNode";
```

#### `src/features/workflow-builder/WorkflowToolbar.tsx`

```tsx
import React from "react";

interface Props {
  onAddStep: () => void;
  onAddDecision: () => void;
  onExportYAML: () => void;
  onExportJSON: () => void;
  onRun: () => void;
  isRunning: boolean;
}

export function WorkflowToolbar({ onAddStep, onAddDecision, onExportYAML, onExportJSON, onRun, isRunning }: Props) {
  return (
    <div className="flex items-center gap-2 ml-auto">
      <button
        onClick={onAddStep}
        className="px-2.5 py-1.5 rounded bg-gray-700 hover:bg-gray-600 text-gray-300 text-xs font-medium transition-colors"
      >
        + Step
      </button>
      <button
        onClick={onAddDecision}
        className="px-2.5 py-1.5 rounded bg-gray-700 hover:bg-gray-600 text-gray-300 text-xs font-medium transition-colors"
      >
        + Decision
      </button>
      <div className="w-px h-5 bg-gray-700" />
      <button
        onClick={onExportYAML}
        className="px-2.5 py-1.5 rounded bg-gray-700 hover:bg-gray-600 text-gray-300 text-xs font-medium transition-colors"
      >
        Export YAML
      </button>
      <button
        onClick={onExportJSON}
        className="px-2.5 py-1.5 rounded bg-gray-700 hover:bg-gray-600 text-gray-300 text-xs font-medium transition-colors"
      >
        Export JSON
      </button>
      <div className="w-px h-5 bg-gray-700" />
      <button
        onClick={onRun}
        disabled={isRunning}
        className="px-3 py-1.5 rounded bg-green-600 hover:bg-green-500 text-white text-xs font-semibold disabled:opacity-50 transition-colors"
      >
        {isRunning ? "Running…" : "▶ Run"}
      </button>
    </div>
  );
}
```

#### `src/features/workflow-builder/hooks/useWorkflowActions.ts`

```typescript
/**
 * useWorkflowActions — business logic for WorkflowBuilderPage.
 * Handles generate-from-goal, run-workflow, and export.
 */

import { useCallback } from "react";
import type { Node, Edge } from "@xyflow/react";

interface Args {
  nodes: Node[];
  edges: Edge[];
  setNodes: (updater: (nodes: Node[]) => Node[]) => void;
  setEdges: (updater: (edges: Edge[]) => Edge[]) => void;
  goalText: string;
}

const API_KEY = () => localStorage.getItem("agentverse_api_key") ?? "";
const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export function useWorkflowActions({ nodes, edges, setNodes, setEdges, goalText }: Args) {
  /**
   * Submit goal with dry_run=true, parse the plan into ReactFlow nodes/edges.
   */
  const generateFromGoal = useCallback(async () => {
    const resp = await fetch(`${BASE_URL}/goals`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": API_KEY() },
      body: JSON.stringify({ goal: goalText, dry_run: true }),
    });
    if (!resp.ok) throw new Error(`Failed to generate plan: ${resp.status}`);
    const data = await resp.json();

    const steps: string[] = data.steps ?? data.plan?.steps ?? [];
    if (steps.length === 0) return;

    const spacing = 120;
    const newNodes: Node[] = [
      {
        id: "start-gen",
        type: "start",
        position: { x: 300, y: 50 },
        data: { label: "Start", goal: goalText },
      },
      ...steps.map((step, i) => ({
        id: `step-gen-${i}`,
        type: "step" as const,
        position: { x: 300, y: 50 + (i + 1) * spacing },
        data: { label: step, toolName: _extractToolName(step), args: "{}" },
      })),
      {
        id: "end-gen",
        type: "end",
        position: { x: 300, y: 50 + (steps.length + 1) * spacing },
        data: { label: "End" },
      },
    ];

    const newEdges: Edge[] = newNodes.slice(0, -1).map((node, i) => ({
      id: `edge-gen-${i}`,
      source: node.id,
      target: newNodes[i + 1]!.id,
      animated: true,
    }));

    setNodes(() => newNodes);
    setEdges(() => newEdges);
  }, [goalText, setNodes, setEdges]);

  /**
   * Convert current graph back to a goal and submit it.
   */
  const runWorkflow = useCallback(async (): Promise<string> => {
    const stepNodes = nodes.filter((n) => n.type === "step");
    const structuredGoal = stepNodes
      .map((n, i) => `${i + 1}. ${String(n.data.label)}`)
      .join("\n");

    const resp = await fetch(`${BASE_URL}/goals`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": API_KEY() },
      body: JSON.stringify({ goal: structuredGoal }),
    });
    if (!resp.ok) throw new Error(`Failed to run workflow: ${resp.status}`);
    const data = await resp.json();
    return data.goal_id as string;
  }, [nodes]);

  /**
   * Export current graph as YAML agent manifest.
   */
  const exportYAML = useCallback(() => {
    const manifest = _buildManifest(nodes, edges, goalText);
    const yaml = _toYAML(manifest);
    _download(yaml, "workflow.yaml", "text/yaml");
  }, [nodes, edges, goalText]);

  /**
   * Export current graph as JSON agent manifest.
   */
  const exportJSON = useCallback(() => {
    const manifest = _buildManifest(nodes, edges, goalText);
    _download(JSON.stringify(manifest, null, 2), "workflow.json", "application/json");
  }, [nodes, edges, goalText]);

  return { generateFromGoal, runWorkflow, exportYAML, exportJSON };
}

// ─── Helpers ────────────────────────────────────────────────────────────────

function _extractToolName(step: string): string {
  const match = step.match(/(?:use|call|run|execute)\s+(\w+)/i);
  return match?.[1] ?? "";
}

function _buildManifest(nodes: Node[], edges: Edge[], goal: string) {
  return {
    apiVersion: "agentverse.ai/v1",
    kind: "Workflow",
    metadata: { name: "generated-workflow", goal },
    spec: {
      nodes: nodes.map((n) => ({
        id: n.id,
        type: n.type,
        data: n.data,
      })),
      edges: edges.map((e) => ({
        source: e.source,
        target: e.target,
        label: e.label,
      })),
    },
  };
}

function _toYAML(obj: object): string {
  // Minimal YAML serialiser (avoids external dependency)
  function yamlify(val: unknown, indent = 0): string {
    const pad = "  ".repeat(indent);
    if (val === null || val === undefined) return "null";
    if (typeof val === "string") return val.includes(":") ? `"${val}"` : val;
    if (typeof val === "number" || typeof val === "boolean") return String(val);
    if (Array.isArray(val)) {
      return val.map((item) => `${pad}- ${yamlify(item, indent + 1)}`).join("\n");
    }
    return Object.entries(val as Record<string, unknown>)
      .map(([k, v]) => {
        if (typeof v === "object" && !Array.isArray(v) && v !== null) {
          return `${pad}${k}:\n${yamlify(v, indent + 1)}`;
        }
        if (Array.isArray(v)) {
          return `${pad}${k}:\n${yamlify(v, indent + 1)}`;
        }
        return `${pad}${k}: ${yamlify(v, indent + 1)}`;
      })
      .join("\n");
  }
  return yamlify(obj);
}

function _download(content: string, filename: string, mimeType: string) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
```

---

### 3.2 Agent Playground

#### `src/features/playground/PlaygroundPage.tsx`

```tsx
/**
 * PlaygroundPage — interactive step-by-step goal execution sandbox.
 *
 * Left panel:  Goal input + agent selector + mock tools JSON editor
 * Right panel: Execution trace (plan → each step → output → cost)
 *
 * "Step" button: POST /enterprise/simulation with step index
 * "Run All":     POST /goals with real or mocked tools
 */

import React, { useState, useRef } from "react";
import { ExecutionTrace } from "./ExecutionTrace";
import { PlaygroundControls } from "./PlaygroundControls";
import { usePlayground } from "./hooks/usePlayground";

export function PlaygroundPage() {
  const [goalText, setGoalText] = useState("");
  const [mockToolsJson, setMockToolsJson] = useState('{\n  "read_file": {"content": "Hello world"}\n}');
  const [selectedAgent, setSelectedAgent] = useState<string>("");
  const [jsonError, setJsonError] = useState<string | null>(null);

  const {
    plan,
    steps,
    currentStepIndex,
    isRunning,
    isStepping,
    totalCost,
    handleStep,
    handleRunAll,
    handleEditPlan,
    reset,
    editMode,
    setEditMode,
    editedPlan,
    setEditedPlan,
  } = usePlayground({ goalText, mockToolsJson, agentId: selectedAgent });

  const validateJson = (value: string) => {
    try {
      JSON.parse(value);
      setJsonError(null);
    } catch {
      setJsonError("Invalid JSON");
    }
    setMockToolsJson(value);
  };

  return (
    <div className="flex h-screen bg-gray-950 overflow-hidden">
      {/* Left Panel */}
      <div className="w-96 flex flex-col border-r border-gray-800 bg-gray-900 shrink-0">
        <div className="px-4 py-3 border-b border-gray-800">
          <h1 className="text-white font-semibold text-lg">Playground</h1>
          <p className="text-gray-500 text-xs mt-0.5">Step through goal execution interactively</p>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* Goal input */}
          <div>
            <label className="block text-gray-400 text-xs font-medium mb-1">Goal</label>
            <textarea
              value={goalText}
              onChange={(e) => setGoalText(e.target.value)}
              placeholder="Enter a goal to execute…"
              rows={4}
              className="w-full bg-gray-800 border border-gray-700 rounded-md text-white text-sm p-2.5 placeholder-gray-500 resize-none focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>

          {/* Agent selector */}
          <div>
            <label className="block text-gray-400 text-xs font-medium mb-1">Agent</label>
            <select
              value={selectedAgent}
              onChange={(e) => setSelectedAgent(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-md text-white text-sm p-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              <option value="">Default Agent</option>
              <option value="supervised">Supervised</option>
              <option value="autonomous">Autonomous</option>
            </select>
          </div>

          {/* Mock tools */}
          <div>
            <label className="block text-gray-400 text-xs font-medium mb-1">
              Mock Tools (JSON)
              {jsonError && <span className="text-red-400 ml-2 text-xs">{jsonError}</span>}
            </label>
            <textarea
              value={mockToolsJson}
              onChange={(e) => validateJson(e.target.value)}
              rows={8}
              spellCheck={false}
              className="w-full bg-gray-800 border border-gray-700 rounded-md text-green-400 text-xs font-mono p-2.5 resize-none focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
        </div>

        {/* Controls */}
        <div className="p-4 border-t border-gray-800">
          <PlaygroundControls
            hasPlan={plan.length > 0}
            currentStep={currentStepIndex}
            totalSteps={plan.length}
            isRunning={isRunning}
            isStepping={isStepping}
            onStep={handleStep}
            onRunAll={handleRunAll}
            onReset={reset}
            onEditPlan={() => setEditMode(!editMode)}
            editMode={editMode}
            goalText={goalText}
            disabled={!goalText.trim() || !!jsonError}
          />
          {totalCost > 0 && (
            <div className="mt-2 text-gray-500 text-xs text-right">
              Cost so far: <span className="text-yellow-400">${totalCost.toFixed(6)}</span>
            </div>
          )}
        </div>
      </div>

      {/* Right Panel — Execution Trace */}
      <div className="flex-1 overflow-y-auto">
        <ExecutionTrace
          plan={plan}
          steps={steps}
          currentStepIndex={currentStepIndex}
          editMode={editMode}
          editedPlan={editedPlan}
          onEditedPlanChange={setEditedPlan}
        />
      </div>
    </div>
  );
}
```

#### `src/features/playground/ExecutionTrace.tsx`

```tsx
import React from "react";

interface StepResult {
  stepIndex: number;
  toolName?: string;
  input?: Record<string, unknown>;
  output?: string;
  status: "pending" | "running" | "complete" | "error";
  durationMs?: number;
}

interface Props {
  plan: string[];
  steps: StepResult[];
  currentStepIndex: number;
  editMode: boolean;
  editedPlan: string[];
  onEditedPlanChange: (plan: string[]) => void;
}

export function ExecutionTrace({
  plan,
  steps,
  currentStepIndex,
  editMode,
  editedPlan,
  onEditedPlanChange,
}: Props) {
  if (plan.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-gray-600">
        <div className="text-center">
          <div className="text-4xl mb-3">🤖</div>
          <p className="text-lg">Enter a goal and click Step or Run All</p>
          <p className="text-sm mt-1">The execution plan will appear here</p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-3 max-w-4xl">
      <h2 className="text-white font-semibold text-base mb-4">
        Execution Plan
        {editMode && (
          <span className="ml-2 text-xs text-amber-400 font-normal">(editing)</span>
        )}
      </h2>

      {plan.map((step, i) => {
        const result = steps.find((s) => s.stepIndex === i);
        const isActive = i === currentStepIndex;
        const isComplete = result?.status === "complete";
        const isError = result?.status === "error";
        const isRunning = result?.status === "running";

        return (
          <div
            key={i}
            className={`
              rounded-lg border p-4 transition-all duration-200
              ${isActive && !isComplete ? "border-indigo-500 bg-indigo-900/20 shadow-indigo-900/30 shadow-lg" : ""}
              ${isComplete ? "border-green-800 bg-green-900/10" : ""}
              ${isError ? "border-red-800 bg-red-900/10" : ""}
              ${!isActive && !isComplete && !isError ? "border-gray-800 bg-gray-900" : ""}
            `}
          >
            {/* Step header */}
            <div className="flex items-start gap-3">
              <div
                className={`
                  w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold shrink-0 mt-0.5
                  ${isComplete ? "bg-green-600 text-white" : ""}
                  ${isError ? "bg-red-600 text-white" : ""}
                  ${isRunning ? "bg-indigo-600 text-white animate-pulse" : ""}
                  ${!isComplete && !isError && !isRunning ? "bg-gray-700 text-gray-400" : ""}
                `}
              >
                {isComplete ? "✓" : isError ? "✗" : isRunning ? "…" : i + 1}
              </div>

              <div className="flex-1 min-w-0">
                {editMode ? (
                  <input
                    value={editedPlan[i] ?? step}
                    onChange={(e) => {
                      const updated = [...editedPlan];
                      updated[i] = e.target.value;
                      onEditedPlanChange(updated);
                    }}
                    className="w-full bg-gray-800 border border-amber-600/50 rounded px-2 py-1 text-white text-sm focus:outline-none focus:ring-1 focus:ring-amber-500"
                  />
                ) : (
                  <span className="text-white text-sm">{step}</span>
                )}

                {result?.toolName && (
                  <span className="ml-2 text-xs text-indigo-400 font-mono bg-indigo-900/30 px-1.5 py-0.5 rounded">
                    🔧 {result.toolName}
                  </span>
                )}
              </div>

              {result?.durationMs && (
                <span className="text-gray-500 text-xs shrink-0">{result.durationMs}ms</span>
              )}
            </div>

            {/* Step output */}
            {result?.output && (
              <div className="mt-3 ml-9 bg-gray-800/50 rounded p-2.5 text-gray-300 text-xs font-mono whitespace-pre-wrap break-words max-h-48 overflow-y-auto">
                {result.output}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
```

#### `src/features/playground/PlaygroundControls.tsx`

```tsx
import React from "react";

interface Props {
  hasPlan: boolean;
  currentStep: number;
  totalSteps: number;
  isRunning: boolean;
  isStepping: boolean;
  onStep: () => void;
  onRunAll: () => void;
  onReset: () => void;
  onEditPlan: () => void;
  editMode: boolean;
  goalText: string;
  disabled: boolean;
}

export function PlaygroundControls({
  hasPlan, currentStep, totalSteps, isRunning, isStepping,
  onStep, onRunAll, onReset, onEditPlan, editMode, goalText, disabled,
}: Props) {
  const isFinished = hasPlan && currentStep >= totalSteps;

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <button
          onClick={onStep}
          disabled={disabled || isRunning || isFinished}
          className="flex-1 py-2 rounded-md bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {isStepping ? "Stepping…" : hasPlan ? "▶ Step" : "▶ Step (Generate Plan)"}
        </button>
        <button
          onClick={onRunAll}
          disabled={disabled || isRunning}
          className="flex-1 py-2 rounded-md bg-green-600 hover:bg-green-500 text-white text-sm font-medium disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {isRunning ? "Running…" : "⚡ Run All"}
        </button>
      </div>

      <div className="flex gap-2">
        {hasPlan && (
          <button
            onClick={onEditPlan}
            className={`flex-1 py-1.5 rounded text-xs font-medium transition-colors ${
              editMode
                ? "bg-amber-600/20 border border-amber-600 text-amber-400"
                : "bg-gray-800 hover:bg-gray-700 text-gray-400 border border-gray-700"
            }`}
          >
            {editMode ? "Done Editing" : "✏️ Edit Plan"}
          </button>
        )}
        <button
          onClick={onReset}
          className="flex-1 py-1.5 rounded bg-gray-800 hover:bg-gray-700 text-gray-400 text-xs border border-gray-700 transition-colors"
        >
          Reset
        </button>
      </div>

      {hasPlan && (
        <div className="text-xs text-gray-600 text-center">
          Step {Math.min(currentStep + 1, totalSteps)} of {totalSteps}
        </div>
      )}
    </div>
  );
}
```

#### `src/features/playground/hooks/usePlayground.ts`

```typescript
import { useState, useCallback } from "react";

interface StepResult {
  stepIndex: number;
  toolName?: string;
  output?: string;
  status: "pending" | "running" | "complete" | "error";
  durationMs?: number;
}

interface Args {
  goalText: string;
  mockToolsJson: string;
  agentId: string;
}

const API_KEY = () => localStorage.getItem("agentverse_api_key") ?? "";
const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export function usePlayground({ goalText, mockToolsJson, agentId }: Args) {
  const [plan, setPlan] = useState<string[]>([]);
  const [steps, setSteps] = useState<StepResult[]>([]);
  const [currentStepIndex, setCurrentStepIndex] = useState(-1);
  const [isRunning, setIsRunning] = useState(false);
  const [isStepping, setIsStepping] = useState(false);
  const [totalCost, setTotalCost] = useState(0);
  const [editMode, setEditMode] = useState(false);
  const [editedPlan, setEditedPlan] = useState<string[]>([]);

  const _parseMockTools = (): Record<string, unknown> => {
    try {
      return JSON.parse(mockToolsJson) as Record<string, unknown>;
    } catch {
      return {};
    }
  };

  const _generatePlan = useCallback(async (): Promise<string[]> => {
    const resp = await fetch(`${BASE_URL}/goals`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": API_KEY() },
      body: JSON.stringify({ goal: goalText, dry_run: true, agent_id: agentId || undefined }),
    });
    if (!resp.ok) throw new Error(`Plan generation failed: ${resp.status}`);
    const data = await resp.json();
    return (data.steps ?? data.plan?.steps ?? []) as string[];
  }, [goalText, agentId]);

  const handleStep = useCallback(async () => {
    setIsStepping(true);
    try {
      // First step: generate the plan
      let activePlan = plan;
      if (activePlan.length === 0) {
        const generatedPlan = await _generatePlan();
        setPlan(generatedPlan);
        setEditedPlan(generatedPlan);
        activePlan = generatedPlan;
        setCurrentStepIndex(0);
        return;
      }

      const nextIdx = currentStepIndex + 1;
      if (nextIdx >= activePlan.length) return;

      // Mark step as running
      setSteps((prev) => [
        ...prev.filter((s) => s.stepIndex !== nextIdx),
        { stepIndex: nextIdx, status: "running" },
      ]);
      setCurrentStepIndex(nextIdx);

      const mockTools = _parseMockTools();
      const stepText = activePlan[nextIdx] ?? "";
      const start = Date.now();

      // Call simulation endpoint
      const resp = await fetch(`${BASE_URL}/enterprise/simulation`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": API_KEY() },
        body: JSON.stringify({
          goal: goalText,
          step_index: nextIdx,
          step_text: stepText,
          mock_tools: mockTools,
        }),
      });

      const durationMs = Date.now() - start;

      if (!resp.ok) {
        setSteps((prev) => [
          ...prev.filter((s) => s.stepIndex !== nextIdx),
          { stepIndex: nextIdx, status: "error", output: `HTTP ${resp.status}`, durationMs },
        ]);
        return;
      }

      const result = await resp.json();
      setSteps((prev) => [
        ...prev.filter((s) => s.stepIndex !== nextIdx),
        {
          stepIndex: nextIdx,
          status: "complete",
          toolName: result.tool_name as string | undefined,
          output: (result.output ?? result.result ?? "") as string,
          durationMs,
        },
      ]);
      setTotalCost((c) => c + ((result.cost_usd as number) ?? 0));
    } finally {
      setIsStepping(false);
    }
  }, [plan, currentStepIndex, goalText, mockToolsJson, agentId, _generatePlan]);

  const handleRunAll = useCallback(async () => {
    setIsRunning(true);
    try {
      let activePlan = plan;
      if (activePlan.length === 0) {
        activePlan = await _generatePlan();
        setPlan(activePlan);
        setEditedPlan(activePlan);
      }
      // Run all remaining steps sequentially
      for (let i = Math.max(currentStepIndex + 1, 0); i < activePlan.length; i++) {
        setCurrentStepIndex(i);
        setSteps((prev) => [...prev.filter((s) => s.stepIndex !== i), { stepIndex: i, status: "running" }]);
        const mockTools = _parseMockTools();
        const resp = await fetch(`${BASE_URL}/enterprise/simulation`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-API-Key": API_KEY() },
          body: JSON.stringify({
            goal: goalText,
            step_index: i,
            step_text: activePlan[i],
            mock_tools: mockTools,
          }),
        });
        if (!resp.ok) {
          setSteps((prev) => [...prev.filter((s) => s.stepIndex !== i), { stepIndex: i, status: "error" }]);
          break;
        }
        const result = await resp.json();
        setSteps((prev) => [
          ...prev.filter((s) => s.stepIndex !== i),
          { stepIndex: i, status: "complete", toolName: result.tool_name, output: result.output ?? result.result ?? "", durationMs: 0 },
        ]);
        setTotalCost((c) => c + ((result.cost_usd as number) ?? 0));
      }
    } finally {
      setIsRunning(false);
    }
  }, [plan, currentStepIndex, goalText, mockToolsJson, agentId, _generatePlan]);

  const handleEditPlan = useCallback(() => setEditMode((m) => !m), []);

  const reset = useCallback(() => {
    setPlan([]);
    setSteps([]);
    setCurrentStepIndex(-1);
    setTotalCost(0);
    setEditMode(false);
    setEditedPlan([]);
  }, []);

  return {
    plan, steps, currentStepIndex, isRunning, isStepping, totalCost,
    handleStep, handleRunAll, handleEditPlan, reset,
    editMode, setEditMode, editedPlan, setEditedPlan,
  };
}
```

---

### 3.3 Push Notifications (In-App + Browser)

#### `src/services/notifications.ts`

```typescript
/**
 * Notification service — connects to SSE event stream and dispatches
 * browser push notifications and in-app bell notifications.
 */

import { useState, useEffect, useCallback, useRef } from "react";

export interface AppNotification {
  id: string;
  type: "goal_completed" | "goal_failed" | "hitl_required" | "agent_error";
  title: string;
  body: string;
  goalId?: string;
  timestamp: Date;
  read: boolean;
}

const API_KEY = () => localStorage.getItem("agentverse_api_key") ?? "";
const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export function useNotifications() {
  const [notifications, setNotifications] = useState<AppNotification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [browserPermission, setBrowserPermission] = useState<NotificationPermission>("default");
  const esRef = useRef<EventSource | null>(null);

  // Request browser permission on mount
  useEffect(() => {
    if ("Notification" in window) {
      setBrowserPermission(Notification.permission);
      if (Notification.permission === "default") {
        Notification.requestPermission().then(setBrowserPermission);
      }
    }
  }, []);

  // Subscribe to global SSE event stream
  useEffect(() => {
    const apiKey = API_KEY();
    if (!apiKey) return;

    const url = `${BASE_URL}/events?api_key=${encodeURIComponent(apiKey)}`;
    const es = new EventSource(url);
    esRef.current = es;

    es.onmessage = (event: MessageEvent<string>) => {
      try {
        const data = JSON.parse(event.data) as Record<string, unknown>;
        const notification = _eventToNotification(data);
        if (notification) {
          addNotification(notification);
        }
      } catch {
        // ignore parse errors
      }
    };

    es.onerror = () => {
      // EventSource auto-reconnects
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, []);

  // Update unread count whenever notifications change
  useEffect(() => {
    setUnreadCount(notifications.filter((n) => !n.read).length);
  }, [notifications]);

  const addNotification = useCallback((notification: AppNotification) => {
    setNotifications((prev) => [notification, ...prev].slice(0, 100)); // keep last 100
    _sendBrowserNotification(notification);
  }, []);

  const markRead = useCallback((id: string) => {
    setNotifications((prev) =>
      prev.map((n) => (n.id === id ? { ...n, read: true } : n)),
    );
  }, []);

  const markAllRead = useCallback(() => {
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
  }, []);

  const dismiss = useCallback((id: string) => {
    setNotifications((prev) => prev.filter((n) => n.id !== id));
  }, []);

  const dismissAll = useCallback(() => {
    setNotifications([]);
  }, []);

  return {
    notifications,
    unreadCount,
    markRead,
    markAllRead,
    dismiss,
    dismissAll,
    browserPermission,
  };
}

// ─── Helpers ────────────────────────────────────────────────────────────────

function _eventToNotification(data: Record<string, unknown>): AppNotification | null {
  const type = data.type as string;
  const goalId = data.goal_id as string | undefined;
  const id = `notif-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;

  switch (type) {
    case "goal_complete":
      return {
        id, type: "goal_completed",
        title: "Goal Completed",
        body: `Goal "${String(data.goal ?? goalId ?? "").slice(0, 60)}" finished successfully.`,
        goalId, timestamp: new Date(), read: false,
      };
    case "goal_failed":
      return {
        id, type: "goal_failed",
        title: "Goal Failed",
        body: `Goal "${String(data.goal ?? goalId ?? "").slice(0, 60)}" failed: ${String(data.reason ?? "unknown error")}`,
        goalId, timestamp: new Date(), read: false,
      };
    case "waiting_approval":
      return {
        id, type: "hitl_required",
        title: "Approval Required",
        body: `Goal "${String(goalId ?? "").slice(0, 40)}" is waiting for your approval.`,
        goalId, timestamp: new Date(), read: false,
      };
    case "agent_error":
      return {
        id, type: "agent_error",
        title: "Agent Error",
        body: String(data.message ?? "An agent encountered an error.").slice(0, 100),
        goalId, timestamp: new Date(), read: false,
      };
    default:
      return null;
  }
}

function _sendBrowserNotification(notification: AppNotification): void {
  if (!("Notification" in window) || Notification.permission !== "granted") return;

  const icons: Record<AppNotification["type"], string> = {
    goal_completed: "✅",
    goal_failed: "❌",
    hitl_required: "🔔",
    agent_error: "⚠️",
  };

  new Notification(`${icons[notification.type]} ${notification.title}`, {
    body: notification.body,
    tag: notification.goalId ?? notification.id,
    icon: "/favicon.ico",
  });
}
```

#### `src/components/TopBar/NotificationBell.tsx`

```tsx
import React, { useState, useRef, useEffect } from "react";
import { useNotifications } from "../../services/notifications";
import type { AppNotification } from "../../services/notifications";

export function NotificationBell() {
  const { notifications, unreadCount, markRead, markAllRead, dismiss } = useNotifications();
  const [isOpen, setIsOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);

  // Close panel on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const typeColors: Record<AppNotification["type"], string> = {
    goal_completed: "text-green-400",
    goal_failed: "text-red-400",
    hitl_required: "text-amber-400",
    agent_error: "text-orange-400",
  };

  return (
    <div className="relative" ref={panelRef}>
      <button
        onClick={() => setIsOpen((o) => !o)}
        className="relative p-2 rounded-lg text-gray-400 hover:text-white hover:bg-gray-800 transition-colors"
        aria-label="Notifications"
      >
        <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
        </svg>
        {unreadCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-red-500 text-white text-[10px] font-bold">
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>

      {isOpen && (
        <div className="absolute right-0 top-full mt-2 w-80 bg-gray-900 border border-gray-700 rounded-xl shadow-2xl z-50 overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
            <h3 className="text-white text-sm font-semibold">Notifications</h3>
            {notifications.length > 0 && (
              <button
                onClick={markAllRead}
                className="text-indigo-400 text-xs hover:text-indigo-300"
              >
                Mark all read
              </button>
            )}
          </div>

          <div className="max-h-80 overflow-y-auto divide-y divide-gray-800">
            {notifications.length === 0 ? (
              <div className="px-4 py-6 text-center text-gray-500 text-sm">
                No notifications
              </div>
            ) : (
              notifications.map((n) => (
                <div
                  key={n.id}
                  onClick={() => markRead(n.id)}
                  className={`px-4 py-3 cursor-pointer hover:bg-gray-800/50 transition-colors ${!n.read ? "bg-gray-800/30" : ""}`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      <p className={`text-sm font-medium ${typeColors[n.type]}`}>
                        {!n.read && <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 inline-block mr-1.5 mb-0.5" />}
                        {n.title}
                      </p>
                      <p className="text-gray-400 text-xs mt-0.5 line-clamp-2">{n.body}</p>
                      <p className="text-gray-600 text-xs mt-1">{n.timestamp.toLocaleTimeString()}</p>
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); dismiss(n.id); }}
                      className="text-gray-600 hover:text-gray-400 text-xs shrink-0"
                    >
                      ×
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
```

---

### 3.4 Real-Time Token Streaming in GoalDetailPage

#### `src/features/goals/hooks/useTokenStream.ts`

```typescript
/**
 * useTokenStream — subscribes to token-level SSE events for live typing effect.
 * Buffers tokens and updates display on animation frames to avoid thrashing.
 */

import { useState, useEffect, useRef, useCallback } from "react";

const API_KEY = () => localStorage.getItem("agentverse_api_key") ?? "";
const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export function useTokenStream(goalId: string | null, stepIndex: number | null) {
  const [streamedText, setStreamedText] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const bufferRef = useRef("");
  const rafRef = useRef<number>(0);
  const esRef = useRef<EventSource | null>(null);

  const flush = useCallback(() => {
    if (bufferRef.current) {
      setStreamedText((prev) => prev + bufferRef.current);
      bufferRef.current = "";
    }
    rafRef.current = 0;
  }, []);

  useEffect(() => {
    if (!goalId || stepIndex === null) return;

    setStreamedText("");
    setIsStreaming(true);
    bufferRef.current = "";

    const url = `${BASE_URL}/goals/${goalId}/stream/tokens?step=${stepIndex}&api_key=${encodeURIComponent(API_KEY())}`;
    const es = new EventSource(url);
    esRef.current = es;

    es.onmessage = (event: MessageEvent<string>) => {
      const raw = event.data.trim();
      if (raw === "[DONE]") {
        setIsStreaming(false);
        es.close();
        // Final flush
        if (rafRef.current) cancelAnimationFrame(rafRef.current);
        flush();
        return;
      }
      try {
        const payload = JSON.parse(raw) as Record<string, unknown>;
        const token = String(payload.token ?? "");
        bufferRef.current += token;
        // Batch updates on animation frame (max ~60fps)
        if (!rafRef.current) {
          rafRef.current = requestAnimationFrame(flush);
        }
      } catch {
        // ignore
      }
    };

    es.onerror = () => {
      setIsStreaming(false);
    };

    return () => {
      es.close();
      esRef.current = null;
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [goalId, stepIndex, flush]);

  return { streamedText, isStreaming };
}
```

#### `src/features/goals/components/LiveTokenDisplay.tsx`

```tsx
/**
 * LiveTokenDisplay — shows live token stream with blinking cursor.
 */

import React, { useRef, useEffect } from "react";
import { useTokenStream } from "../hooks/useTokenStream";

interface Props {
  goalId: string;
  stepIndex: number;
  className?: string;
}

export function LiveTokenDisplay({ goalId, stepIndex, className = "" }: Props) {
  const { streamedText, isStreaming } = useTokenStream(goalId, stepIndex);
  const containerRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom as tokens arrive
  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [streamedText]);

  if (!streamedText && !isStreaming) return null;

  return (
    <div
      ref={containerRef}
      className={`bg-gray-900 rounded-lg p-3 text-gray-200 text-sm font-mono whitespace-pre-wrap break-words max-h-64 overflow-y-auto ${className}`}
    >
      {streamedText}
      {isStreaming && (
        <span className="inline-block w-0.5 h-4 bg-indigo-400 ml-0.5 align-middle animate-[blink_1s_step-end_infinite]" />
      )}
    </div>
  );
}
```

Add to `tailwind.config.js`:

```javascript
// In extend.keyframes:
blink: {
  "0%, 100%": { opacity: "1" },
  "50%": { opacity: "0" },
},
// In extend.animation:
"[blink_1s_step-end_infinite]": "blink 1s step-end infinite",
```

---

### 3.5 Mobile-Responsive Design

#### Updated `src/components/Layout/Sidebar.tsx` (mobile-responsive changes)

```tsx
/**
 * Responsive Sidebar:
 * - Desktop (≥ 768px): vertical left sidebar (existing behaviour)
 * - Mobile (< 768px):  horizontal bottom tab bar
 */

import React from "react";
import { NavLink, useLocation } from "react-router-dom";

const NAV_ITEMS = [
  { to: "/",           label: "Dashboard",  icon: "⊞" },
  { to: "/goals",      label: "Goals",      icon: "🎯" },
  { to: "/agents",     label: "Agents",     icon: "🤖" },
  { to: "/connectors", label: "Connectors", icon: "🔌" },
  { to: "/playground", label: "Playground", icon: "🧪" },
  { to: "/workflow-builder", label: "Builder", icon: "⬡" },
  { to: "/settings",   label: "Settings",   icon: "⚙️" },
] as const;

export function Sidebar() {
  const location = useLocation();

  return (
    <>
      {/* Desktop sidebar */}
      <aside className="hidden md:flex flex-col w-56 shrink-0 bg-gray-900 border-r border-gray-800 h-screen sticky top-0">
        <div className="px-4 py-4 border-b border-gray-800">
          <span className="text-white font-bold text-lg tracking-tight">AgentVerse</span>
        </div>
        <nav className="flex-1 p-3 space-y-0.5 overflow-y-auto">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-indigo-600/20 text-indigo-400"
                    : "text-gray-400 hover:text-white hover:bg-gray-800"
                }`
              }
            >
              <span className="text-base">{item.icon}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      {/* Mobile bottom tab bar */}
      <nav className="md:hidden fixed bottom-0 left-0 right-0 z-50 bg-gray-900 border-t border-gray-800 safe-area-inset-bottom">
        <div className="flex items-stretch h-16">
          {NAV_ITEMS.slice(0, 5).map((item) => {
            const isActive = item.to === "/"
              ? location.pathname === "/"
              : location.pathname.startsWith(item.to);
            return (
              <NavLink
                key={item.to}
                to={item.to}
                className={`flex-1 flex flex-col items-center justify-center gap-0.5 text-xs transition-colors ${
                  isActive ? "text-indigo-400" : "text-gray-500 hover:text-gray-300"
                }`}
              >
                <span className="text-xl leading-none">{item.icon}</span>
                <span className="text-[10px] font-medium">{item.label}</span>
              </NavLink>
            );
          })}
        </div>
      </nav>

      {/* Mobile bottom padding spacer (so content isn't hidden behind tab bar) */}
      <div className="md:hidden h-16 shrink-0" />
    </>
  );
}
```

#### `index.html` viewport meta (add if missing)

```html
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
<meta name="mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
```

#### Mobile GoalDetailPage layout (add CSS classes)

```tsx
// In GoalDetailPage.tsx — update main container class:
// Before: className="flex gap-6 p-6"
// After:
className="flex flex-col md:flex-row gap-4 md:gap-6 p-4 md:p-6"

// Left/right panels should stack on mobile:
// Before: className="w-72 shrink-0"
// After:
className="w-full md:w-72 md:shrink-0"
```

#### Mobile ApprovalsPage cards

```tsx
// In ApprovalsPage.tsx — card update:
// Before: className="p-6 rounded-lg bg-gray-900 border border-gray-800"
// After:
className="p-4 md:p-6 rounded-lg bg-gray-900 border border-gray-800 touch-manipulation"

// Approve/Reject buttons — larger tap targets on mobile:
className="w-full md:w-auto py-3 md:py-2 px-6 rounded-lg bg-green-600 hover:bg-green-500 text-white font-medium text-base md:text-sm active:scale-95 transition-all touch-manipulation"
```

#### Mobile DashboardPage KPI cards

```tsx
// DashboardPage layout:
// Before: className="grid grid-cols-4 gap-4"
// After:
className="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4"
```

---

## 4. Package.json Addition

```json
{
  "dependencies": {
    "@xyflow/react": "^12.0.0"
  }
}
```

---

## 5. Router Updates

**File:** `src/App.tsx` — add new routes:

```tsx
import { WorkflowBuilderPage } from "./features/workflow-builder/WorkflowBuilderPage";
import { PlaygroundPage } from "./features/playground/PlaygroundPage";

// Inside <Routes>:
<Route path="/workflow-builder" element={<WorkflowBuilderPage />} />
<Route path="/playground" element={<PlaygroundPage />} />
```

---

## 6. Tests

### `tests/features/workflow-builder/WorkflowBuilderPage.test.tsx`

```tsx
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { WorkflowBuilderPage } from "../../../src/features/workflow-builder/WorkflowBuilderPage";

// Mock @xyflow/react
vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ children }: { children: React.ReactNode }) => <div data-testid="reactflow">{children}</div>,
  Controls: () => <div />,
  MiniMap: () => <div />,
  Background: () => <div />,
  addEdge: vi.fn((params, eds) => [...eds, params]),
  useNodesState: (init: unknown[]) => [init, vi.fn(), vi.fn()],
  useEdgesState: (init: unknown[]) => [init, vi.fn(), vi.fn()],
  Handle: () => <div />,
  Position: { Top: "top", Bottom: "bottom", Left: "left", Right: "right" },
}));

describe("WorkflowBuilderPage", () => {
  beforeEach(() => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ steps: ["Read file", "Summarise"], goal_id: "g1" }),
    });
  });

  it("renders without crashing", () => {
    render(
      <MemoryRouter>
        <WorkflowBuilderPage />
      </MemoryRouter>,
    );
    expect(screen.getByText("Workflow Builder")).toBeTruthy();
  });

  it("shows generate button", () => {
    render(
      <MemoryRouter>
        <WorkflowBuilderPage />
      </MemoryRouter>,
    );
    expect(screen.getByText("Generate")).toBeTruthy();
  });

  it("generate button disabled when goal is empty", () => {
    render(
      <MemoryRouter>
        <WorkflowBuilderPage />
      </MemoryRouter>,
    );
    const btn = screen.getByText("Generate") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });
});
```

### `tests/features/playground/PlaygroundPage.test.tsx`

```tsx
import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { PlaygroundPage } from "../../../src/features/playground/PlaygroundPage";

describe("PlaygroundPage", () => {
  it("renders left panel and empty trace", () => {
    render(
      <MemoryRouter>
        <PlaygroundPage />
      </MemoryRouter>,
    );
    expect(screen.getByText("Playground")).toBeTruthy();
    expect(screen.getByPlaceholderText(/Enter a goal/i)).toBeTruthy();
    expect(screen.getByText(/Enter a goal and click Step/i)).toBeTruthy();
  });

  it("shows Run All and Step buttons", () => {
    render(
      <MemoryRouter>
        <PlaygroundPage />
      </MemoryRouter>,
    );
    expect(screen.getByText(/Run All/i)).toBeTruthy();
    expect(screen.getByText(/Step/i)).toBeTruthy();
  });
});
```

### `tests/services/notifications.test.ts`

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

// Mock EventSource
const mockEventSource = {
  onmessage: null as ((e: MessageEvent) => void) | null,
  onerror: null as (() => void) | null,
  close: vi.fn(),
};

vi.stubGlobal("EventSource", vi.fn(() => mockEventSource));
vi.stubGlobal("Notification", {
  permission: "denied",
  requestPermission: vi.fn().mockResolvedValue("denied"),
});

import { useNotifications } from "../../src/services/notifications";

describe("useNotifications", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("starts with zero notifications", () => {
    const { result } = renderHook(() => useNotifications());
    expect(result.current.notifications).toHaveLength(0);
    expect(result.current.unreadCount).toBe(0);
  });

  it("markAllRead sets all to read", () => {
    const { result } = renderHook(() => useNotifications());
    // Manually inject a notification via the event source mock
    act(() => {
      if (mockEventSource.onmessage) {
        mockEventSource.onmessage(
          new MessageEvent("message", {
            data: JSON.stringify({
              type: "goal_complete",
              goal_id: "g1",
              goal: "Test goal",
            }),
          }),
        );
      }
    });
    act(() => result.current.markAllRead());
    expect(result.current.notifications.every((n) => n.read)).toBe(true);
    expect(result.current.unreadCount).toBe(0);
  });
});
```

### `tests/features/goals/useTokenStream.test.ts`

```typescript
import { describe, it, expect, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";

const mockEs = {
  onmessage: null as ((e: MessageEvent) => void) | null,
  onerror: null as (() => void) | null,
  close: vi.fn(),
};

vi.stubGlobal("EventSource", vi.fn(() => mockEs));
vi.stubGlobal("requestAnimationFrame", (cb: () => void) => { cb(); return 0; });
vi.stubGlobal("cancelAnimationFrame", vi.fn());

import { useTokenStream } from "../../src/features/goals/hooks/useTokenStream";

describe("useTokenStream", () => {
  it("accumulates tokens", () => {
    const { result } = renderHook(() => useTokenStream("goal-1", 0));
    expect(result.current.isStreaming).toBe(true);

    act(() => {
      mockEs.onmessage?.(new MessageEvent("message", { data: JSON.stringify({ token: "Hello " }) }));
      mockEs.onmessage?.(new MessageEvent("message", { data: JSON.stringify({ token: "world" }) }));
    });

    expect(result.current.streamedText).toContain("Hello ");
    expect(result.current.streamedText).toContain("world");
  });

  it("stops streaming on [DONE]", () => {
    const { result } = renderHook(() => useTokenStream("goal-2", 0));
    act(() => {
      mockEs.onmessage?.(new MessageEvent("message", { data: "[DONE]" }));
    });
    expect(result.current.isStreaming).toBe(false);
  });
});
```

---

## 7. Acceptance Criteria

```bash
# Install @xyflow/react
cd agent-verse-frontend && npm install @xyflow/react

# Run all frontend tests
npx vitest run

# Verify new routes are accessible
npm run dev &
sleep 5
curl -s http://localhost:5173/workflow-builder | grep -q "AgentVerse" && echo "WorkflowBuilder OK"
curl -s http://localhost:5173/playground | grep -q "AgentVerse" && echo "Playground OK"

# Mobile layout: open Chrome DevTools → 375px width → verify bottom tab bar appears
# Sidebar: inspect DOM → confirm .md:flex class controls desktop visibility

# Token streaming: submit a goal, open GoalDetailPage, 
# watch tokens appear letter-by-letter during step execution

# Notification bell: complete a goal → bell badge shows "1" unread
# Click bell → notification with goal title appears
# Click notification → marks as read
```
