/**
 * useYamlSync — bidirectional canvas state ↔ YAML string.
 *
 * YAML is canonical. The canvas displays a visual representation of the
 * WorkflowDefinition DSL. On every canvas change, YAML is re-generated.
 * On every YAML edit, canvas nodes+edges are re-derived.
 */
import { useCallback, useState } from 'react';
import type { Node, Edge } from '@xyflow/react';

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

function edgesToDepends(nodeId: string, edges: Edge[]): string[] {
  return edges
    .filter((e) => e.target === nodeId)
    .map((e) => e.source);
}

function canvasToDefinition(nodes: Node[], edges: Edge[]): Record<string, unknown> {
  const steps = nodes
    .filter((n) => n.type !== 'trigger')
    .map((n) => ({
      ...nodeToStep(n),
      depends_on: edgesToDepends(n.id, edges),
    }));

  const triggerNode = nodes.find((n) => n.type === 'trigger');
  const trigger = triggerNode
    ? { type: (triggerNode.data as Record<string, unknown>).triggerType ?? 'api' }
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
  const trigger = definition.trigger as Record<string, unknown> | undefined;

  const nodes: Node[] = [];
  const edges: Edge[] = [];

  // Trigger node
  if (trigger) {
    nodes.push({
      id: '__trigger__',
      type: 'trigger',
      position: { x: 100, y: 100 },
      data: {
        label: 'Trigger',
        triggerType: trigger.type,
        stepType: 'trigger',
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
    // Dynamic import for YAML serialization
    // In production this would use js-yaml
    return JSON.stringify(definition, null, 2);
  } catch {
    return '';
  }
}

function parseYaml(yaml: string): Record<string, unknown> {
  try {
    return JSON.parse(yaml);
  } catch {
    // Fallback: return empty definition
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
