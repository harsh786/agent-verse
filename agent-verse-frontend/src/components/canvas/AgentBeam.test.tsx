import { render } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { AgentBeam } from './AgentBeam';

// Control reduced-motion so we can exercise both the rendered and the
// null (motion-off) branches deterministically.
const motionState = vi.hoisted(() => ({ reduced: false }));
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  return { ...actual, useReducedMotion: () => motionState.reduced };
});

afterEach(() => {
  motionState.reduced = false;
});

describe('AgentBeam', () => {
  test('draws a quadratic bezier path from `from` to `to`', () => {
    const { container } = render(<AgentBeam from={{ x: 10, y: 20 }} to={{ x: 110, y: 220 }} />);
    const path = container.querySelector('path.beam-path') as SVGPathElement;
    expect(path).not.toBeNull();
    // midX = (10+110)/2 = 60, midY = (20+220)/2 - 40 = 80
    expect(path.getAttribute('d')).toBe('M 10 20 Q 60 80 110 220');
  });

  test('applies the default cyan stroke and a custom color', () => {
    const { container, rerender } = render(<AgentBeam from={{ x: 0, y: 0 }} to={{ x: 50, y: 50 }} />);
    expect(container.querySelector('path.beam-path')?.getAttribute('stroke')).toBe('#00D4FF');

    rerender(<AgentBeam from={{ x: 0, y: 0 }} to={{ x: 50, y: 50 }} color="#FF3366" />);
    expect(container.querySelector('path.beam-path')?.getAttribute('stroke')).toBe('#FF3366');
  });

  test('sizes the svg from the width/height props', () => {
    const { container } = render(
      <AgentBeam from={{ x: 0, y: 0 }} to={{ x: 1, y: 1 }} width={800} height={300} />,
    );
    const svg = container.querySelector('svg') as SVGSVGElement;
    expect(svg.getAttribute('width')).toBe('800');
    expect(svg.getAttribute('height')).toBe('300');
  });

  test('injects an animated beam dot travelling the path', () => {
    const { container } = render(<AgentBeam from={{ x: 0, y: 0 }} to={{ x: 100, y: 0 }} />);
    const dot = container.querySelector('circle.beam-dot');
    expect(dot).not.toBeNull();
    expect(dot?.getAttribute('fill')).toBe('#00D4FF');
  });

  test('renders nothing when reduced motion is requested', () => {
    motionState.reduced = true;
    const { container } = render(<AgentBeam from={{ x: 0, y: 0 }} to={{ x: 10, y: 10 }} />);
    expect(container.querySelector('svg')).toBeNull();
  });
});
