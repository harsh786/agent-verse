import { render } from '@testing-library/react';
import { act, createRef } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { ParticleCanvas, type ParticleCanvasRef, type ParticleShape } from './ParticleCanvas';

// Make reduced-motion deterministic per test.
const reduce = vi.fn(() => false);
vi.mock('framer-motion', () => ({ useReducedMotion: () => reduce() }));

// A fake 2D context recording the draw calls the render loop makes (jsdom has
// no canvas backend).
function makeCtx() {
  return {
    clearRect: vi.fn(),
    save: vi.fn(),
    restore: vi.fn(),
    beginPath: vi.fn(),
    arc: vi.fn(),
    fill: vi.fn(),
    stroke: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    closePath: vi.fn(),
    globalAlpha: 0,
    fillStyle: '',
    strokeStyle: '',
    shadowColor: '',
    shadowBlur: 0,
    lineWidth: 0,
  };
}

let frameCb: FrameRequestCallback | null = null;

function particle(shape: ParticleShape, over: Partial<Record<string, unknown>> = {}) {
  return {
    id: `p-${shape}`,
    from: { x: 0, y: 0 },
    to: { x: 100, y: 100 },
    color: '#00D4FF',
    size: 3,
    trail: false,
    trailLength: 0,
    duration: 1000,
    shape,
    ...over,
  };
}

beforeEach(() => {
  reduce.mockReturnValue(false);
  frameCb = null;
  // Capture the render callback instead of auto-running it, so a particle can be
  // emitted before the frame executes.
  vi.spyOn(globalThis, 'requestAnimationFrame').mockImplementation((cb: FrameRequestCallback) => {
    frameCb = cb;
    return 1;
  });
  vi.spyOn(globalThis, 'cancelAnimationFrame').mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
  reduce.mockReturnValue(false);
});

describe('ParticleCanvas', () => {
  test('sizes the canvas from its width/height props and hides it from a11y', () => {
    const { container } = render(<ParticleCanvas width={640} height={480} className="extra" />);
    const canvas = container.querySelector('canvas')!;
    expect(canvas.getAttribute('width')).toBe('640');
    expect(canvas.getAttribute('height')).toBe('480');
    expect(canvas.getAttribute('aria-hidden')).toBe('true');
    expect(canvas.className).toContain('extra');
    expect(canvas.className).toContain('absolute');
  });

  test('schedules an animation frame on mount and clears the canvas each frame', () => {
    const ctx = makeCtx();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
      ctx as unknown as CanvasRenderingContext2D,
    );
    render(<ParticleCanvas width={100} height={100} />);
    expect(frameCb).not.toBeNull();
    act(() => frameCb!(performance.now()));
    expect(ctx.clearRect).toHaveBeenCalled();
  });

  test('emitParticle causes the particle to be drawn on the next frame', () => {
    const ctx = makeCtx();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
      ctx as unknown as CanvasRenderingContext2D,
    );
    const ref = createRef<ParticleCanvasRef>();
    render(<ParticleCanvas ref={ref} width={100} height={100} />);

    act(() => ref.current!.emitParticle(particle('circle') as never));
    act(() => frameCb!(performance.now()));
    // A circle particle draws an arc + fill with its color.
    expect(ctx.arc).toHaveBeenCalled();
    expect(ctx.fill).toHaveBeenCalled();
    expect(ctx.fillStyle).toBe('#00D4FF');
  });

  test('draws the stroke-based shapes (spark / x) via stroke calls', () => {
    const ctx = makeCtx();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
      ctx as unknown as CanvasRenderingContext2D,
    );
    const ref = createRef<ParticleCanvasRef>();
    render(<ParticleCanvas ref={ref} width={100} height={100} />);

    act(() => {
      ref.current!.emitParticle(particle('spark') as never);
      ref.current!.emitParticle(particle('x') as never);
      ref.current!.emitParticle(particle('diamond') as never);
      ref.current!.emitParticle(particle('star') as never);
    });
    act(() => frameCb!(performance.now()));
    expect(ctx.stroke).toHaveBeenCalled();
    expect(ctx.moveTo).toHaveBeenCalled();
    expect(ctx.closePath).toHaveBeenCalled();
  });

  test('emitBurst spawns multiple particles that draw on the next frame', () => {
    const ctx = makeCtx();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
      ctx as unknown as CanvasRenderingContext2D,
    );
    const ref = createRef<ParticleCanvasRef>();
    render(<ParticleCanvas ref={ref} width={100} height={100} />);

    act(() => ref.current!.emitBurst({ x: 50, y: 50 }, '#FF0000', 6));
    act(() => frameCb!(performance.now()));
    // Burst particles are sparks → stroked arms + a filled core.
    expect(ctx.stroke).toHaveBeenCalled();
    expect(ctx.fill).toHaveBeenCalled();
  });

  test('clearAll removes every particle so nothing is drawn afterwards', () => {
    const ctx = makeCtx();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
      ctx as unknown as CanvasRenderingContext2D,
    );
    const ref = createRef<ParticleCanvasRef>();
    render(<ParticleCanvas ref={ref} width={100} height={100} />);

    act(() => ref.current!.emitParticle(particle('circle') as never));
    act(() => ref.current!.clearAll());
    ctx.arc.mockClear();
    act(() => frameCb!(performance.now()));
    // Canvas is still cleared, but with no live particles nothing is drawn.
    expect(ctx.clearRect).toHaveBeenCalled();
    expect(ctx.arc).not.toHaveBeenCalled();
  });

  test('under reduced motion it does not schedule frames or emit particles', () => {
    reduce.mockReturnValue(true);
    const ctx = makeCtx();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
      ctx as unknown as CanvasRenderingContext2D,
    );
    const ref = createRef<ParticleCanvasRef>();
    render(<ParticleCanvas ref={ref} width={100} height={100} />);
    // Effect returns early → no frame captured.
    expect(frameCb).toBeNull();
    // emitParticle is a no-op under reduced motion; even a manual (nonexistent)
    // frame would draw nothing.
    act(() => ref.current!.emitParticle(particle('circle') as never));
    expect(ctx.arc).not.toHaveBeenCalled();
  });
});
