import { render } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { TokenFlowOverlay } from './TokenFlowOverlay';

// Same deterministic reduced-motion mock as the sibling test — the component
// only imports useReducedMotion from framer-motion.
const reducedMotion = vi.fn(() => false);
vi.mock('framer-motion', () => ({ useReducedMotion: () => reducedMotion() }));

const BASE = { isActive: false, width: 320, height: 200, fromX: 0, fromY: 0, toX: 100, toY: 80 } as const;

afterEach(() => {
  reducedMotion.mockReturnValue(false);
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe('TokenFlowOverlay — additional branches', () => {
  test('forwards a custom className onto the canvas alongside the base classes', () => {
    const { container } = render(<TokenFlowOverlay {...BASE} className="opacity-50" />);
    const canvas = container.querySelector('canvas')!;
    expect(canvas.className).toContain('opacity-50');
    expect(canvas.className).toContain('absolute');
    expect(canvas.className).toContain('inset-0');
  });

  test('updates the canvas width/height attributes when the size props change', () => {
    const { container, rerender } = render(<TokenFlowOverlay {...BASE} />);
    rerender(<TokenFlowOverlay {...BASE} width={640} height={480} />);
    const canvas = container.querySelector('canvas')!;
    expect(canvas.getAttribute('width')).toBe('640');
    expect(canvas.getAttribute('height')).toBe('480');
  });

  test('re-running the effect with new flow coordinates keeps the canvas mounted', () => {
    const { container, rerender } = render(<TokenFlowOverlay {...BASE} isActive tps={30} />);
    expect(container.querySelector('canvas')).not.toBeNull();
    // Changing the from/to endpoints re-runs the animation effect (new deps).
    rerender(<TokenFlowOverlay {...BASE} isActive tps={30} fromX={50} fromY={60} toX={300} toY={220} />);
    expect(container.querySelector('canvas')).not.toBeNull();
  });

  test('unmounting while active tears down the animation frame without throwing', () => {
    // A truthy 2D context lets the effect schedule a frame, so the cleanup path
    // (cancelAnimationFrame) is exercised on unmount. jsdom otherwise returns null.
    const ctx = {
      clearRect: vi.fn(), save: vi.fn(), restore: vi.fn(),
      beginPath: vi.fn(), arc: vi.fn(), fill: vi.fn(),
      globalAlpha: 0, fillStyle: '', shadowColor: '', shadowBlur: 0,
    };
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
      ctx as unknown as CanvasRenderingContext2D,
    );
    // Schedule exactly once (return a real id) without recursing.
    vi.spyOn(globalThis, 'requestAnimationFrame').mockReturnValue(1 as unknown as number);
    const cancelSpy = vi.spyOn(globalThis, 'cancelAnimationFrame').mockImplementation(() => {});

    const { container, unmount } = render(<TokenFlowOverlay {...BASE} isActive tps={40} />);
    expect(container.querySelector('canvas')).not.toBeNull();
    expect(() => unmount()).not.toThrow();
    expect(cancelSpy).toHaveBeenCalledWith(1);
  });

  test('draws token particles onto the 2D context when active with a nonzero tps', () => {
    // Provide a fake 2D context (jsdom has no canvas backend) and drive a few
    // animation frames so the spawn + draw branch actually executes.
    const ctx = {
      clearRect: vi.fn(),
      save: vi.fn(),
      restore: vi.fn(),
      beginPath: vi.fn(),
      arc: vi.fn(),
      fill: vi.fn(),
      globalAlpha: 0,
      fillStyle: '',
      shadowColor: '',
      shadowBlur: 0,
    };
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
      ctx as unknown as CanvasRenderingContext2D,
    );

    let rafCount = 0;
    vi.spyOn(globalThis, 'requestAnimationFrame').mockImplementation((cb: FrameRequestCallback) => {
      // Invoke a handful of frames with advancing timestamps, then stop so the
      // recursive rAF loop terminates.
      if (rafCount < 4) {
        rafCount += 1;
        cb(rafCount * 100);
      }
      return rafCount;
    });
    vi.spyOn(globalThis, 'cancelAnimationFrame').mockImplementation(() => {});

    render(<TokenFlowOverlay {...BASE} isActive tps={25} />);

    expect(ctx.clearRect).toHaveBeenCalled();
    // Particles were spawned (gap > spawnInterval on the first frame) and drawn.
    expect(ctx.arc).toHaveBeenCalled();
    expect(ctx.fill).toHaveBeenCalled();
    expect(ctx.fillStyle).toBe('#00D4FF');
  });
});
