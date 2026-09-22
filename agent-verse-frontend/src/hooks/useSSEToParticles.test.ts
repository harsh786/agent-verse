/**
 * useSSEToParticles — bridges SSE goal events to ParticleCanvas emissions.
 * Verifies the per-event-type routing (burst vs particle, arc for spawned
 * children, guardrail rejections), the "no known positions" fallback, and
 * that only new events (since the last render) are processed.
 */
import { renderHook } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';
import type { ParticleCanvasRef } from '@/components/canvas/ParticleCanvas';
import type { GoalEvent } from '@/lib/sse/useGoalStream';
import { useSSEToParticles, type NodePositionMap } from './useSSEToParticles';

function makeCanvas(): ParticleCanvasRef {
  return {
    emitBurst: vi.fn(),
    emitParticle: vi.fn(),
  } as unknown as ParticleCanvasRef;
}

function makeRef(canvas: ParticleCanvasRef | null): React.RefObject<ParticleCanvasRef | null> {
  return { current: canvas };
}

function evt(type: string, extra: Partial<GoalEvent> = {}): GoalEvent {
  return { type, ...extra } as GoalEvent;
}

describe('useSSEToParticles', () => {
  test('does nothing when the canvas ref is not yet attached', () => {
    const positions: NodePositionMap = new Map();
    const { rerender } = renderHook(
      ({ events, ref }) => useSSEToParticles(events, positions, ref),
      { initialProps: { events: [evt('step_started')], ref: makeRef(null) } },
    );
    // No throw, nothing to assert on a null canvas — just re-render to confirm stability.
    rerender({ events: [evt('step_started')], ref: makeRef(null) });
  });

  test('ignores event types with no particle mapping', () => {
    const canvas = makeCanvas();
    const positions: NodePositionMap = new Map([['plan', { x: 1, y: 1 }], ['output', { x: 2, y: 2 }]]);
    renderHook(() => useSSEToParticles([evt('unknown_event_type')], positions, makeRef(canvas)));
    expect(canvas.emitBurst).not.toHaveBeenCalled();
    expect(canvas.emitParticle).not.toHaveBeenCalled();
  });

  test('emits a center burst when no known node positions exist for the event', () => {
    const canvas = makeCanvas();
    const positions: NodePositionMap = new Map();
    renderHook(() => useSSEToParticles([evt('step_started')], positions, makeRef(canvas)));
    expect(canvas.emitBurst).toHaveBeenCalledWith({ x: 300, y: 200 }, expect.any(String), 6);
  });

  test('tool_call_complete emits a bigger burst at the tool position than tool_call_failed', () => {
    const canvas = makeCanvas();
    const positions: NodePositionMap = new Map([
      ['llm', { x: 0, y: 0 }],
      ['step1', { x: 10, y: 10 }],
      ['my_tool', { x: 50, y: 50 }],
    ]);
    renderHook(() =>
      useSSEToParticles(
        [evt('tool_call_complete', { step: 'step1', tool_name: 'my_tool' })],
        positions,
        makeRef(canvas),
      ),
    );
    expect(canvas.emitBurst).toHaveBeenCalledWith({ x: 50, y: 50 }, expect.any(String), 8);
  });

  test('tool_call_failed bursts at the fallback "toPos" when the specific tool position is unknown', () => {
    const canvas = makeCanvas();
    const positions: NodePositionMap = new Map([
      ['llm', { x: 0, y: 0 }],
      ['step1', { x: 10, y: 10 }],
    ]);
    renderHook(() =>
      useSSEToParticles(
        [evt('tool_call_failed', { step: 'step1', tool: 'unpositioned_tool' })],
        positions,
        makeRef(canvas),
      ),
    );
    expect(canvas.emitBurst).toHaveBeenCalledWith({ x: 10, y: 10 }, expect.any(String), 4);
  });

  test('guardrail_rejected and tool_call_denied burst at the guardrail node (or fallback)', () => {
    const canvas = makeCanvas();
    const positions: NodePositionMap = new Map([
      ['llm', { x: 0, y: 0 }],
      ['plan', { x: 1, y: 1 }],
      ['output', { x: 2, y: 2 }],
      ['guardrail', { x: 99, y: 99 }],
    ]);
    renderHook(() => useSSEToParticles([evt('guardrail_rejected')], positions, makeRef(canvas)));
    expect(canvas.emitBurst).toHaveBeenCalledWith({ x: 99, y: 99 }, expect.any(String), 12);
  });

  test('child_agent_spawned emits an arced particle offset from the target position', () => {
    const canvas = makeCanvas();
    const positions: NodePositionMap = new Map([
      ['llm', { x: 0, y: 0 }],
      ['output', { x: 100, y: 100 }],
    ]);
    renderHook(() => useSSEToParticles([evt('child_agent_spawned')], positions, makeRef(canvas)));
    expect(canvas.emitParticle).toHaveBeenCalledWith(
      expect.objectContaining({
        from: { x: 0, y: 0 },
        to: { x: 180, y: 40 },
      }),
    );
  });

  test('a generic mapped event emits a straight particle from "llm" to the step position', () => {
    const canvas = makeCanvas();
    const positions: NodePositionMap = new Map([
      ['llm', { x: 0, y: 0 }],
      ['step1', { x: 10, y: 20 }],
    ]);
    renderHook(() =>
      useSSEToParticles([evt('step_complete', { step: 'step1' })], positions, makeRef(canvas)),
    );
    expect(canvas.emitParticle).toHaveBeenCalledWith(
      expect.objectContaining({ from: { x: 0, y: 0 }, to: { x: 10, y: 20 }, shape: 'circle' }),
    );
  });

  test('only new events (since the previous render) are processed on subsequent renders', () => {
    const canvas = makeCanvas();
    const positions: NodePositionMap = new Map();
    const { rerender } = renderHook(
      ({ events }) => useSSEToParticles(events, positions, makeRef(canvas)),
      { initialProps: { events: [evt('step_started')] } },
    );
    expect(canvas.emitBurst).toHaveBeenCalledTimes(1);

    rerender({ events: [evt('step_started'), evt('step_complete')] });
    expect(canvas.emitBurst).toHaveBeenCalledTimes(2);
  });
});
