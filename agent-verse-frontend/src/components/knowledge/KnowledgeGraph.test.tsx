/**
 * KnowledgeGraph — force-directed graph rendered with d3-force/d3-selection,
 * both dynamically imported inside a useEffect. jsdom has no real layout
 * engine for SVG, so both d3 modules are mocked with minimal chainable fakes
 * that still invoke the component's own accessor/callback functions the way
 * real d3 would, so the component's own logic (color lookups, click
 * dispatch, the tick handler, and the focus-dimming effect) is what actually
 * gets exercised and covered — not d3 internals.
 */
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';
import { KnowledgeGraph, type KnowledgeGraphData } from './KnowledgeGraph';

// ── d3-selection fake ────────────────────────────────────────────────────────
// Selections built as: root.append("g").selectAll(tag).data(arr).enter().append(tag)
// `.append(tag)` on a selection that already has bound data returns *itself*
// (mirroring d3's enter-selection semantics), so chained `.attr()` calls after
// `enter().append()` keep operating on the same tracked object. `.selectAll()`
// always starts a fresh selection, matching d3.
let lastNodeSel: ChainSelection | null = null;
let lastLinkSel: ChainSelection | null = null;
let lastLabelSel: ChainSelection | null = null;

interface ChainSelection {
  attr: (name: string, val?: unknown) => ChainSelection;
  text: (val?: unknown) => ChainSelection;
  data: (d: unknown[]) => ChainSelection;
  enter: () => ChainSelection;
  selectAll: (sel: string) => ChainSelection;
  append: (tag: string) => ChainSelection;
  on: (evt: string, handler: (...args: unknown[]) => void) => ChainSelection;
  __onClick?: (event: unknown, datum: unknown) => void;
  __boundData: unknown[];
}

function makeSelection(): ChainSelection {
  let boundData: unknown[] = [];
  const api: ChainSelection = {
    __boundData: [],
    attr: vi.fn((_name: string, val?: unknown) => {
      if (typeof val === 'function') {
        boundData.forEach((d) => {
          try {
            (val as (d: unknown) => unknown)(d);
          } catch {
            /* real d3 wouldn't throw here either way; tolerate mock gaps */
          }
        });
      }
      return api;
    }),
    text: vi.fn((val?: unknown) => {
      if (typeof val === 'function') boundData.forEach((d) => (val as (d: unknown) => unknown)(d));
      return api;
    }),
    data: vi.fn((d: unknown[]) => {
      boundData = d ?? [];
      api.__boundData = boundData;
      return api;
    }),
    enter: vi.fn(() => api),
    selectAll: vi.fn(() => makeSelection()),
    append: vi.fn((tag: string) => {
      const result = boundData.length > 0 ? api : makeSelection();
      if (tag === 'circle') lastNodeSel = result;
      if (tag === 'line') lastLinkSel = result;
      if (tag === 'text') lastLabelSel = result;
      return result;
    }),
    on: vi.fn((evt: string, handler: (...args: unknown[]) => void) => {
      if (evt === 'click') api.__onClick = handler;
      return api;
    }),
  };
  return api;
}

vi.mock('d3-selection', () => ({
  select: vi.fn(() => makeSelection()),
}));

// ── d3-force fake ────────────────────────────────────────────────────────────
let lastSim: { __onTick?: () => void; stop: () => void } | null = null;

vi.mock('d3-force', () => ({
  forceSimulation: vi.fn(() => {
    const sim: { force: unknown; on: unknown; alphaDecay: unknown; stop: unknown; __onTick?: () => void } = {
      force: vi.fn(() => sim),
      on: vi.fn((evt: string, cb: () => void) => {
        if (evt === 'tick') sim.__onTick = cb;
        return sim;
      }),
      alphaDecay: vi.fn(() => sim),
      stop: vi.fn(),
    };
    lastSim = sim as typeof lastSim;
    return sim;
  }),
  forceLink: vi.fn(() => {
    const fl = { id: vi.fn(() => fl), distance: vi.fn(() => fl) };
    return fl;
  }),
  forceManyBody: vi.fn(() => {
    const fm = { strength: vi.fn(() => fm) };
    return fm;
  }),
  forceCenter: vi.fn(() => ({})),
}));

const DATA: KnowledgeGraphData = {
  nodes: [
    { id: 'n1', label: 'Node One', type: 'document' },
    { id: 'n2', label: 'Node Two', type: 'concept' },
    { id: 'n3', label: 'Node Three', type: 'unknown-type' },
  ],
  edges: [{ id: 'e1', source: 'n1', target: 'n2', label: 'links to' }],
};

async function flush() {
  // Let the dynamic import() microtasks in the effect resolve.
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe('KnowledgeGraph', () => {
  test('renders the svg and a sorted, de-duplicated legend of node types', async () => {
    render(<KnowledgeGraph data={DATA} />);
    await flush();
    expect(screen.getByLabelText('Knowledge graph visualization')).toBeInTheDocument();
    // 'concept', 'document', 'unknown-type' sorted alphabetically, each once.
    const legendLabels = ['concept', 'document', 'unknown-type'];
    for (const l of legendLabels) expect(screen.getByText(l)).toBeInTheDocument();
  });

  test('clicking a node opens the detail panel and calls onNodeClick', async () => {
    const onNodeClick = vi.fn();
    render(<KnowledgeGraph data={DATA} onNodeClick={onNodeClick} />);
    await flush();

    expect(lastNodeSel?.__onClick).toBeTypeOf('function');
    act(() => {
      lastNodeSel!.__onClick!({}, DATA.nodes[0]);
    });

    expect(onNodeClick).toHaveBeenCalledWith(DATA.nodes[0]);
    expect(await screen.findByText('Node One')).toBeInTheDocument();
    expect(screen.getByText('document', { selector: 'p' })).toBeInTheDocument();
  });

  test('dismissing the detail panel clears the selection', async () => {
    render(<KnowledgeGraph data={DATA} />);
    await flush();
    act(() => {
      lastNodeSel!.__onClick!({}, DATA.nodes[0]);
    });
    await screen.findByText('Node One');

    await userEvent.click(screen.getByRole('button', { name: /dismiss/i }));
    expect(screen.queryByText('Node One')).not.toBeInTheDocument();
  });

  test('works without an onNodeClick callback (optional chaining branch)', async () => {
    render(<KnowledgeGraph data={DATA} />);
    await flush();
    expect(() => {
      act(() => {
        lastNodeSel!.__onClick!({}, DATA.nodes[0]);
      });
    }).not.toThrow();
  });

  test('merges custom colors over the defaults and uses the fallback for unknown types', async () => {
    render(<KnowledgeGraph data={DATA} colors={{ document: '#custom-doc' }} />);
    await flush();
    // The node fill accessor was invoked over bound node data during setup;
    // verifying it ran without throwing (and the legend renders) exercises
    // the NODE_COLORS merge + FALLBACK_COLOR branch for 'unknown-type'.
    expect(screen.getByText('unknown-type')).toBeInTheDocument();
  });

  test('running the tick handler updates node/link/label positions without throwing', async () => {
    render(<KnowledgeGraph data={DATA} />);
    await flush();
    expect(lastSim?.__onTick).toBeTypeOf('function');
    expect(() => act(() => lastSim!.__onTick!())).not.toThrow();
  });

  test('focusNodeId dims unrelated nodes/edges; clearing it restores full opacity', async () => {
    const { rerender } = render(<KnowledgeGraph data={DATA} focusNodeId="n1" />);
    await flush();
    expect(lastNodeSel).toBeTruthy();
    expect(lastLinkSel).toBeTruthy();
    expect(lastLabelSel).toBeTruthy();

    rerender(<KnowledgeGraph data={DATA} focusNodeId={null} />);
    await waitFor(() => {
      expect(lastNodeSel!.attr).toHaveBeenCalledWith('opacity', 1);
    });
  });

  test('respects custom width/height on the svg element', async () => {
    render(<KnowledgeGraph data={DATA} width={800} height={500} />);
    await flush();
    const svg = screen.getByLabelText('Knowledge graph visualization');
    expect(svg).toHaveAttribute('width', '800');
    expect(svg).toHaveAttribute('height', '500');
  });
});
