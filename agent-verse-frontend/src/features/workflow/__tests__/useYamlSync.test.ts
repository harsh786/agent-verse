/**
 * Tests for useYamlSync — bidirectional canvas ↔ YAML sync.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useYamlSync } from '../builder/canvas-utils/useYamlSync';
import type { Node } from '@xyflow/react';

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
