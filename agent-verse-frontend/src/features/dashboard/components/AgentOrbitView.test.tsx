/**
 * Tests for AgentOrbitView — the d3-force powered agent activity SVG.
 *
 * The component lazily imports d3-force and builds the SVG imperatively inside
 * an async effect, so the DOM-assembly assertions wait for that to settle.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { AgentOrbitView } from './AgentOrbitView';

function wait(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

type Agent = { id: string; label: string; status: 'active' | 'idle' | 'error'; goalCount: number };

const AGENTS: Agent[] = [
  { id: 'a1', label: 'Researcher', status: 'active', goalCount: 3 },
  { id: 'a2', label: 'Writer', status: 'idle', goalCount: 0 },
];

afterEach(() => vi.restoreAllMocks());

describe('AgentOrbitView', () => {
  test('renders an empty-state message when there are no agents', () => {
    render(<AgentOrbitView agents={[]} />);
    expect(screen.getByText('No active agents')).toBeInTheDocument();
    expect(screen.queryByLabelText('Agent activity visualization')).not.toBeInTheDocument();
  });

  test('renders an accessible svg with default dimensions when agents are present', () => {
    render(<AgentOrbitView agents={AGENTS} />);
    const svg = screen.getByLabelText('Agent activity visualization');
    expect(svg.tagName.toLowerCase()).toBe('svg');
    expect(svg.getAttribute('role')).toBe('img');
    expect(svg.getAttribute('width')).toBe('320');
    expect(svg.getAttribute('height')).toBe('220');
  });

  test('applies custom width, height and className', () => {
    render(<AgentOrbitView agents={AGENTS} width={500} height={400} className="my-orbit" />);
    const svg = screen.getByLabelText('Agent activity visualization');
    expect(svg.getAttribute('width')).toBe('500');
    expect(svg.getAttribute('height')).toBe('400');
    expect(svg.getAttribute('viewBox')).toBe('0 0 500 400');
    expect(svg.classList.contains('my-orbit')).toBe(true);
  });

  test('builds the force graph (core node + per-agent groups) once d3-force loads', async () => {
    const { container } = render(<AgentOrbitView agents={AGENTS} />);
    const svg = container.querySelector('svg')!;
    // The imperative build appends a <defs>, link lines and node circles.
    await waitFor(() => expect(svg.querySelectorAll('circle').length).toBeGreaterThan(0));
    expect(svg.querySelector('defs')).not.toBeNull();
    // The core node carries the ⚡ glyph.
    await waitFor(() => expect(svg.textContent).toContain('⚡'));
  });

  test('renders each agent label (truncated) inside the svg', async () => {
    const { container } = render(<AgentOrbitView agents={AGENTS} />);
    const svg = container.querySelector('svg')!;
    await waitFor(() => {
      const texts = Array.from(svg.querySelectorAll('text')).map((t) => t.textContent);
      expect(texts).toContain('Researcher');
      expect(texts).toContain('Writer');
    });
  });

  test('renders orbit trail segments for an active agent over successive animation frames', async () => {
    const { container } = render(
      <AgentOrbitView agents={[{ id: 'a1', label: 'Researcher', status: 'active', goalCount: 2 }]} />
    );
    // Trails are only recorded for 'active' agents every 3rd animation frame, and a
    // segment needs >= 2 recorded points, so this needs several real RAF ticks.
    await waitFor(
      () => {
        const trailLines = Array.from(container.querySelectorAll('line')).filter(
          (l) => l.getAttribute('stroke') === '#00D4FF'
        );
        expect(trailLines.length).toBeGreaterThan(0);
      },
      { timeout: 4000, interval: 50 }
    );
    // Let a few more frames run: each frame first clears the previously-drawn
    // trail segments before redrawing, so the DOM keeps reflecting only the
    // latest trail rather than accumulating stale lines forever.
    await wait(150);
    const trailLines = Array.from(container.querySelectorAll('line')).filter(
      (l) => l.getAttribute('stroke') === '#00D4FF'
    );
    expect(trailLines.length).toBeGreaterThan(0);
  });

  test('caps orbit trail history so it does not grow unbounded on a long-lived view', async () => {
    const { container } = render(
      <AgentOrbitView agents={[{ id: 'a1', label: 'Researcher', status: 'active', goalCount: 1 }]} />
    );
    // A point is recorded every 3rd frame and the trail is capped at 20 points,
    // so a segment count needs ~60+ frames (roughly a second of real animation)
    // to prove the cap holds instead of growing indefinitely.
    await waitFor(
      () => {
        const trailLines = Array.from(container.querySelectorAll('line')).filter(
          (l) => l.getAttribute('stroke') === '#00D4FF'
        );
        expect(trailLines.length).toBeGreaterThan(0);
      },
      { timeout: 6000, interval: 100 }
    );
    await wait(2500);
    const trailLines = Array.from(container.querySelectorAll('line')).filter(
      (l) => l.getAttribute('stroke') === '#00D4FF'
    );
    // trail.length is capped at 20, so at most 19 connecting segments render.
    expect(trailLines.length).toBeLessThanOrEqual(19);
  }, 10000);

  test('does not accumulate a trail for a purely idle/error roster', async () => {
    const { container } = render(
      <AgentOrbitView
        agents={[
          { id: 'a1', label: 'Researcher', status: 'idle', goalCount: 0 },
          { id: 'a2', label: 'Writer', status: 'error', goalCount: 1 },
        ]}
      />
    );
    const svg = container.querySelector('svg')!;
    await waitFor(() => expect(svg.querySelectorAll('circle').length).toBeGreaterThan(0));
    // Give the animation loop several frames to run; no trail should ever be drawn.
    await new Promise((r) => setTimeout(r, 300));
    const trailLines = Array.from(container.querySelectorAll('line')).filter(
      (l) => l.getAttribute('stroke') === '#00D4FF'
    );
    expect(trailLines).toHaveLength(0);
  });

  test('draws a beam between a valid communicating pair and silently skips an unknown agent id', async () => {
    const agents: Agent[] = [
      { id: 'a1', label: 'Researcher', status: 'active', goalCount: 1 },
      { id: 'a2', label: 'Writer', status: 'active', goalCount: 1 },
    ];
    const { container } = render(
      <AgentOrbitView
        agents={agents}
        communicatingPairs={[
          ['a1', 'a2'],
          ['ghost-src', 'ghost-tgt'],
        ]}
      />
    );
    await waitFor(
      () => {
        const beamLines = Array.from(container.querySelectorAll('line')).filter(
          (l) => l.getAttribute('stroke') === '#A855F7'
        );
        // Exactly one beam: the valid pair. The unknown-id pair is silently skipped
        // (no thrown error, no phantom beam).
        expect(beamLines).toHaveLength(1);
      },
      { timeout: 4000, interval: 50 }
    );
    // Subsequent frames re-clear and redraw the beam group; it should keep
    // showing exactly the one valid beam rather than duplicating it.
    await wait(150);
    const beamLines = Array.from(container.querySelectorAll('line')).filter(
      (l) => l.getAttribute('stroke') === '#A855F7'
    );
    expect(beamLines).toHaveLength(1);
  });

  test('falls back to the idle color for an agent with an unrecognized/malformed status', async () => {
    const malformedAgents = [
      // Real-world malformed activity: an agent payload with a status value
      // outside the known union (e.g. a backend enum drift or bad event data).
      { id: 'a1', label: 'Ghost', status: 'unknown_status' as unknown as Agent['status'], goalCount: 2 },
    ];
    const { container } = render(<AgentOrbitView agents={malformedAgents} />);
    const svg = container.querySelector('svg')!;
    await waitFor(() => expect(svg.querySelectorAll('circle').length).toBeGreaterThan(0));
    const circles = Array.from(svg.querySelectorAll('circle'));
    // Neither the goal-count ring nor the main node circle recognize the status,
    // so both must fall back to the idle color (#475569) instead of throwing
    // or rendering `undefined`.
    const idleColored = circles.filter((c) => c.getAttribute('stroke') === '#475569' || c.getAttribute('fill') === '#475569');
    expect(idleColored.length).toBeGreaterThan(0);
  });

  test('clears previously-drawn DOM nodes and rebuilds cleanly when props change', async () => {
    const { container, rerender } = render(<AgentOrbitView agents={AGENTS} width={320} />);
    const svg = container.querySelector('svg')!;
    await waitFor(() => expect(svg.querySelectorAll('circle').length).toBeGreaterThan(0));
    const firstBuildCircleCount = svg.querySelectorAll('circle').length;

    // Changing `width` re-runs the build effect on the SAME mounted <svg> node
    // (which still holds the previous build's children) rather than a fresh one.
    rerender(<AgentOrbitView agents={AGENTS} width={480} />);
    await waitFor(() => expect(svg.getAttribute('width')).toBe('480'));
    await waitFor(() => expect(svg.querySelectorAll('circle').length).toBeGreaterThan(0));

    // The rebuild must clear the old DOM before appending new nodes, so the
    // circle count should match a fresh build rather than doubling up.
    expect(svg.querySelectorAll('circle').length).toBe(firstBuildCircleCount);
  });
});
