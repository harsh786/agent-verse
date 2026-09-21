/**
 * Tests for AgentOrbitView's defensive position fallbacks.
 *
 * The render loop reads `node.x ?? cx` / `node.y ?? cy` everywhere it draws
 * link lines, node groups and communication beams, in case d3-force hasn't
 * (yet) assigned coordinates to a node — e.g. a link whose source/target
 * hasn't been resolved from a raw id string into a node object, which is
 * exactly what happens if the simulation's `.force("link", ...)` wiring is
 * ever skipped or misconfigured. That is real defensive code guarding
 * against a broken physics step, not something the default d3-force
 * integration exercises in the happy path, so it's tested here with a
 * lightweight stand-in for d3-force that intentionally leaves node
 * coordinates unresolved.
 */
import { render, waitFor } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';

// The d3-force stub must mimic an arbitrarily chainable builder API, same as
// the untyped `any` usage in AgentOrbitView.tsx itself for the real d3-force objects.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function chainable(onCall?: (prop: string, args: unknown[]) => void): any {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const obj: any = new Proxy(
    {},
    {
      get(_target, prop: string) {
        return (...args: unknown[]) => {
          onCall?.(prop, args);
          return obj;
        };
      },
    }
  );
  return obj;
}

vi.mock('d3-force', () => ({
  // Strip the coordinates the component pre-seeds on each node (as if the
  // simulation never ticked) and never resolve link.source/target away from
  // their raw id strings (as if forceLink's wiring were skipped) — every
  // downstream `?? cx` / `?? cy` fallback should kick in instead of
  // rendering `undefined` or throwing on `"__core__".x`.
  forceSimulation: (nodes: Array<Record<string, unknown>>) => {
    nodes.forEach((n) => {
      delete n.x;
      delete n.y;
    });
    return chainable();
  },
  forceLink: () => chainable(),
  forceManyBody: () => chainable(),
  forceCenter: () => chainable(),
  forceCollide: () => chainable(),
}));

// Import after the mock so the component picks up the stub.
const { AgentOrbitView } = await import('./AgentOrbitView');

describe('AgentOrbitView (unresolved d3-force positions)', () => {
  test('falls back to the center point for links, nodes and beams instead of rendering "undefined"', async () => {
    const agents = [
      { id: 'a1', label: 'Researcher', status: 'active' as const, goalCount: 1 },
      { id: 'a2', label: 'Writer', status: 'idle' as const, goalCount: 0 },
    ];
    const { container } = render(
      <AgentOrbitView agents={agents} width={320} height={220} communicatingPairs={[['a1', 'a2']]} />
    );
    const svg = container.querySelector('svg')!;

    await waitFor(() => expect(svg.querySelectorAll('circle').length).toBeGreaterThan(0));
    await waitFor(() => expect(svg.querySelectorAll('line').length).toBeGreaterThan(0));

    const cx = String(320 / 2);
    const cy = String(220 / 2);

    // Link lines (core -> each agent) must fall back to the center point.
    const linkLines = Array.from(svg.querySelectorAll('line')).filter(
      (l) => l.getAttribute('stroke') === 'rgba(255,255,255,0.07)'
    );
    expect(linkLines.length).toBeGreaterThan(0);
    for (const line of linkLines) {
      expect(line.getAttribute('x1')).toBe(cx);
      expect(line.getAttribute('y1')).toBe(cy);
      expect(line.getAttribute('x2')).toBe(cx);
      expect(line.getAttribute('y2')).toBe(cy);
    }

    // Node groups must be translated to the center point, not "undefined,undefined".
    const groups = Array.from(svg.querySelectorAll('g')).filter((g) => g.getAttribute('transform'));
    expect(groups.length).toBeGreaterThan(0);
    for (const g of groups) {
      expect(g.getAttribute('transform')).toContain(`translate(${cx}, ${cy})`);
    }

    // The communication beam between a1 and a2 must also fall back to the center point.
    await waitFor(() => {
      const beamLines = Array.from(svg.querySelectorAll('line')).filter(
        (l) => l.getAttribute('stroke') === '#A855F7'
      );
      expect(beamLines.length).toBeGreaterThan(0);
      for (const beam of beamLines) {
        expect(beam.getAttribute('x1')).toBe(cx);
        expect(beam.getAttribute('y1')).toBe(cy);
        expect(beam.getAttribute('x2')).toBe(cx);
        expect(beam.getAttribute('y2')).toBe(cy);
      }
    });
  });
});
