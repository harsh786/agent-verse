/**
 * Tests for AgentOrbitView — the d3-force powered agent activity SVG.
 *
 * The component lazily imports d3-force and builds the SVG imperatively inside
 * an async effect, so the DOM-assembly assertions wait for that to settle.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { AgentOrbitView } from './AgentOrbitView';

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
});
