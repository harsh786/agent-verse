/**
 * Tests for useYamlSync — bidirectional canvas ↔ YAML sync.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useYamlSync, canvasToDefinition } from '../builder/canvas-utils/useYamlSync';
import type { Node, Edge } from '@xyflow/react';

const SIMPLE_DEF = JSON.stringify({ name: 'Test', steps: [{ id: 's1', type: 'tool', tool: 't' }] });

describe('useYamlSync', () => {
  it('initializes with empty state when no yaml provided', () => {
    const { result } = renderHook(() => useYamlSync());
    expect(result.current.canvasState.nodes).toEqual([]);
    expect(result.current.canvasState.edges).toEqual([]);
  });

  it('initializes canvas from yaml', () => {
    const { result } = renderHook(() => useYamlSync(SIMPLE_DEF));
    expect(result.current.canvasState.nodes.length).toBeGreaterThan(0);
  });

  it('updateFromYaml parses new yaml and updates canvas', () => {
    const { result } = renderHook(() => useYamlSync());
    act(() => {
      result.current.updateFromYaml(SIMPLE_DEF);
    });
    expect(result.current.yaml).toBe(SIMPLE_DEF);
    expect(result.current.canvasState.nodes.length).toBeGreaterThan(0);
  });

  it('updateFromCanvas generates yaml from nodes', () => {
    const { result } = renderHook(() => useYamlSync());
    const nodes: Node[] = [{
      id: 'tool-1',
      type: 'tool',
      position: { x: 100, y: 100 },
      data: { stepType: 'tool', label: 'My Tool', tool: 'github.create_issue' },
    }];
    act(() => {
      result.current.updateFromCanvas(nodes, []);
    });
    expect(result.current.yaml).toBeTruthy();
    expect(result.current.yaml.length).toBeGreaterThan(0);
  });

  it('error is null for valid yaml', () => {
    const { result } = renderHook(() => useYamlSync(SIMPLE_DEF));
    expect(result.current.error).toBeNull();
    expect(result.current.isValid).toBe(true);
  });

  it('isValid stays true after updateFromCanvas', () => {
    const { result } = renderHook(() => useYamlSync());
    act(() => {
      result.current.updateFromCanvas([], []);
    });
    expect(result.current.isValid).toBe(true);
  });
});

describe('canvasToDefinition', () => {
  const triggerNode: Node = {
    id: '__trigger__',
    type: 'trigger',
    position: { x: 0, y: 0 },
    data: { stepType: 'trigger', label: 'Trigger', triggerType: 'api' },
  };
  const entryStep: Node = {
    id: 'ocr_registration',
    type: 'ocr',
    position: { x: 280, y: 0 },
    data: { stepType: 'ocr', label: 'OCR' },
  };
  const triggerEdge: Edge = {
    id: 'e-trigger-ocr', source: '__trigger__', target: 'ocr_registration', type: 'smoothstep',
  };

  it('does not emit the synthetic __trigger__ node as a step depends_on', () => {
    const def = canvasToDefinition([triggerNode, entryStep], [triggerEdge]);
    const steps = def.steps as Array<Record<string, unknown>>;
    const ocr = steps.find((s) => s.id === 'ocr_registration')!;
    expect(ocr.depends_on).toEqual([]); // NOT ['__trigger__'] — the DSL rejects that
  });

  it('serializes a schedule trigger with its cron (round-trips through node data)', () => {
    const schedTrigger: Node = {
      id: '__trigger__',
      type: 'trigger',
      position: { x: 0, y: 0 },
      data: { stepType: 'trigger', triggerType: 'schedule', cron: '0 9 * * *', timezone: 'UTC' },
    };
    const def = canvasToDefinition([schedTrigger, entryStep], []);
    expect(def.trigger).toEqual({ type: 'schedule', schedule: { cron: '0 9 * * *', timezone: 'UTC' } });
  });

  it('serializes a webhook trigger with its path', () => {
    const webhookTrigger: Node = {
      id: '__trigger__',
      type: 'trigger',
      position: { x: 0, y: 0 },
      data: { stepType: 'trigger', triggerType: 'webhook', webhook_path: '/webhooks/kyc' },
    };
    const def = canvasToDefinition([webhookTrigger, entryStep], []);
    expect(def.trigger).toEqual({ type: 'webhook', webhook: { path: '/webhooks/kyc' } });
  });
});
