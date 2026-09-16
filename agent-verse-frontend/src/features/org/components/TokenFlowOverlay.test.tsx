import { render } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { TokenFlowOverlay } from './TokenFlowOverlay';

// useReducedMotion is mocked so the render branch is deterministic regardless of
// the jsdom matchMedia default. Each test sets the value it needs.
const reducedMotion = vi.fn(() => false);
vi.mock('framer-motion', () => ({ useReducedMotion: () => reducedMotion() }));

const BASE = { isActive: false, width: 320, height: 200, fromX: 0, fromY: 0, toX: 100, toY: 80 } as const;

afterEach(() => {
  reducedMotion.mockReturnValue(false);
  vi.clearAllMocks();
});

describe('TokenFlowOverlay', () => {
  test('renders a canvas sized to the given width and height', () => {
    const { container } = render(<TokenFlowOverlay {...BASE} />);
    const canvas = container.querySelector('canvas');
    expect(canvas).not.toBeNull();
    expect(canvas!.getAttribute('width')).toBe('320');
    expect(canvas!.getAttribute('height')).toBe('200');
  });

  test('the canvas is decorative and does not intercept pointer events', () => {
    const { container } = render(<TokenFlowOverlay {...BASE} isActive />);
    const canvas = container.querySelector('canvas')!;
    expect(canvas.getAttribute('aria-hidden')).toBe('true');
    expect(canvas.className).toContain('pointer-events-none');
  });

  test('toggling isActive keeps the canvas mounted without throwing', () => {
    const { container, rerender } = render(<TokenFlowOverlay {...BASE} isActive={false} />);
    expect(container.querySelector('canvas')).not.toBeNull();
    rerender(<TokenFlowOverlay {...BASE} isActive tps={40} />);
    expect(container.querySelector('canvas')).not.toBeNull();
  });

  test('renders nothing when the user prefers reduced motion', () => {
    reducedMotion.mockReturnValue(true);
    const { container } = render(<TokenFlowOverlay {...BASE} isActive />);
    expect(container.querySelector('canvas')).toBeNull();
  });
});
