/**
 * useYamlSync — bidirectional canvas state ↔ YAML string.
 *
 * YAML is canonical. The canvas displays a visual representation of the
 * WorkflowDefinition DSL. On every canvas change, YAML is re-generated.
 * On every YAML edit, canvas nodes+edges are re-derived.
 */
import { useCallback, useState } from 'react';
import type { Node, Edge } from '@xyflow/react';
import jsYaml from 'js-yaml';

// ── Types ─────────────────────────────────────────────────────────────────────

export interface CanvasState {
  nodes: Node[];
  edges: Edge[];
}

export interface YamlSyncResult {
  yaml: string;
  canvasState: CanvasState;
  error: string | null;
  updateFromCanvas: (nodes: Node[], edges: Edge[]) => void;
  updateFromYaml: (yaml: string) => void;
  isValid: boolean;
}

// ── Canvas → DSL helpers ──────────────────────────────────────────────────────

function nodeToStep(node: Node): Record<string, unknown> {
  const data = node.data as Record<string, unknown>;
  const step: Record<string, unknown> = {
    id: node.id,
    type: data.stepType ?? data.type ?? 'tool',
  };
  // Copy all extra config fields
  for (const [key, val] of Object.entries(data)) {
    if (key !== 'label' && key !== 'stepType' && val !== undefined && val !== '') {
      step[key] = val;
    }
  }
  return step;
}

// Serialize a trigger node's data into a DSL trigger object, preserving the
// schedule cron / webhook path so the visual builder round-trips them (the DSL
// nests these under `schedule` / `webhook`, not on the trigger itself).
function triggerFromData(data: Record<string, unknown>): Record<string, unknown> {
  const type = (data.triggerType as string) ?? 'api';
  if (type === 'schedule') {
    const cron = String(data.cron ?? '').trim();
    return {
      type,
      schedule: { cron, timezone: String(data.timezone ?? 'UTC') },
    };
  }
  if (type === 'webhook') {
    return { type, webhook: { path: String(data.webhook_path ?? '') } };
  }
  return { type };
}

// The trigger is a synthetic canvas node, not a real step, so an edge from it
// must NOT become a `depends_on` entry — the DSL validator rejects a step that
// depends on the unknown step id `__trigger__`.
const TRIGGER_NODE_ID = '__trigger__';

function edgesToDepends(nodeId: string, edges: Edge[]): string[] {
  return edges
    .filter((e) => e.target === nodeId && e.source !== TRIGGER_NODE_ID)
    .map((e) => e.source);
}

export function canvasToDefinition(nodes: Node[], edges: Edge[]): Record<string, unknown> {
  const steps = nodes
    .filter((n) => n.type !== 'trigger')
    .map((n) => ({
      ...nodeToStep(n),
      depends_on: edgesToDepends(n.id, edges),
    }));

  const triggerNode = nodes.find((n) => n.type === 'trigger');
  const trigger = triggerNode
    ? triggerFromData(triggerNode.data as Record<string, unknown>)
    : { type: 'api' };

  return {
    name: (nodes[0]?.data as Record<string, unknown>)?.workflowName ?? 'Untitled',
    trigger,
    steps,
  };
}

// ── DSL → canvas helpers ──────────────────────────────────────────────────────

const H_SPACING = 280;
const V_SPACING = 100;

function layoutNodes(definition: Record<string, unknown>): { nodes: Node[]; edges: Edge[] } {
  const steps = (definition.steps as Record<string, unknown>[] | undefined) ?? [];
  // Accept both the DSL's singular `trigger` and the builder/stored plural
  // `triggers` array, so an existing workflow always shows its trigger node.
  const pluralTriggers = definition.triggers as Record<string, unknown>[] | undefined;
  const trigger =
    (definition.trigger as Record<string, unknown> | undefined) ??
    (Array.isArray(pluralTriggers) && pluralTriggers.length ? pluralTriggers[0] : undefined);

  const nodes: Node[] = [];
  const edges: Edge[] = [];

  // Trigger node
  if (trigger) {
    const schedule = trigger.schedule as Record<string, unknown> | undefined;
    const webhook = trigger.webhook as Record<string, unknown> | undefined;
    nodes.push({
      id: '__trigger__',
      type: 'trigger',
      position: { x: 100, y: 100 },
      data: {
        label: 'Trigger',
        triggerType: trigger.type,
        stepType: 'trigger',
        // Surface the nested schedule/webhook config so the Trigger config
        // panel shows and edits it (round-trips back via triggerFromData).
        cron: schedule?.cron ?? '',
        timezone: schedule?.timezone ?? 'UTC',
        webhook_path: webhook?.path ?? '',
      },
    });
  }

  // Step nodes — simple left-to-right topological layout
  const levelOf: Record<string, number> = {};
  for (const step of steps) {
    const id = step.id as string;
    const deps = (step.depends_on as string[] | undefined) ?? [];
    const level = deps.length === 0 ? 0 : Math.max(...deps.map((d) => (levelOf[d] ?? 0) + 1));
    levelOf[id] = level;
  }

  const columnCounts: Record<number, number> = {};
  for (const step of steps) {
    const id = step.id as string;
    const col = levelOf[id] ?? 0;
    columnCounts[col] = (columnCounts[col] ?? 0) + 1;
  }
  const columnRowIndex: Record<number, number> = {};

  for (const step of steps) {
    const id = step.id as string;
    const col = levelOf[id] ?? 0;
    const row = columnRowIndex[col] ?? 0;
    columnRowIndex[col] = row + 1;

    nodes.push({
      id,
      type: step.type as string,
      position: { x: 100 + (col + (trigger ? 1 : 0)) * H_SPACING, y: 100 + row * V_SPACING },
      data: {
        label: id,
        stepType: step.type,
        ...step,
      },
    });

    const deps = (step.depends_on as string[] | undefined) ?? [];
    for (const dep of deps) {
      edges.push({
        id: `e-${dep}-${id}`,
        source: dep === '__trigger__' ? '__trigger__' : dep,
        target: id,
        type: 'smoothstep',
        animated: false,
      });
    }

    // Connect trigger to entry steps (no deps)
    if (trigger && deps.length === 0) {
      edges.push({
        id: `e-trigger-${id}`,
        source: '__trigger__',
        target: id,
        type: 'smoothstep',
      });
    }
  }

  return { nodes, edges };
}

function definitionToYaml(definition: Record<string, unknown>): string {
  try {
    // Real YAML output (js-yaml). lineWidth:-1 keeps long prompt strings on one
    // line rather than folding them, which round-trips more predictably.
    return jsYaml.dump(definition, { indent: 2, lineWidth: -1, noRefs: true });
  } catch {
    return '';
  }
}

export function parseWorkflowYaml(text: string): Record<string, unknown> {
  // js-yaml load() is safe in v4 (no code execution) and also accepts JSON,
  // since JSON is a subset of YAML — so pasted JSON definitions still parse.
  const parsed = jsYaml.load(text);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { name: 'Untitled', steps: [] };
  }
  return parsed as Record<string, unknown>;
}

function parseYaml(yaml: string): Record<string, unknown> {
  try {
    return parseWorkflowYaml(yaml);
  } catch {
    return { name: 'Untitled', steps: [] };
  }
}

// ── Main hook ─────────────────────────────────────────────────────────────────

export function useYamlSync(initialYaml?: string): YamlSyncResult {
  const [yaml, setYaml] = useState(initialYaml ?? '');
  const [error, setError] = useState<string | null>(null);
  const [canvasState, setCanvasState] = useState<CanvasState>(() => {
    if (!initialYaml) return { nodes: [], edges: [] };
    try {
      const def = parseYaml(initialYaml);
      return layoutNodes(def);
    } catch {
      return { nodes: [], edges: [] };
    }
  });

  const updateFromCanvas = useCallback((nodes: Node[], edges: Edge[]) => {
    try {
      const definition = canvasToDefinition(nodes, edges);
      const newYaml = definitionToYaml(definition);
      setYaml(newYaml);
      setError(null);
    } catch (err) {
      setError(String(err));
    }
  }, []);

  const updateFromYaml = useCallback((newYaml: string) => {
    setYaml(newYaml);
    try {
      const def = parseYaml(newYaml);
      const { nodes, edges } = layoutNodes(def);
      setCanvasState({ nodes, edges });
      setError(null);
    } catch (err) {
      setError(String(err));
    }
  }, []);

  return {
    yaml,
    canvasState,
    error,
    updateFromCanvas,
    updateFromYaml,
    isValid: error === null,
  };
}
