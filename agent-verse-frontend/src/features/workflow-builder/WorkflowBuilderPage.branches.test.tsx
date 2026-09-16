import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';

// ── @xyflow/react stub backed by REAL React state ──────────────────────────────
// The CivilizationPage stub freezes state (setNodes is a no-op), which would make
// add/select/delete untestable. This variant keeps real useState so the page's
// node/edge mutations actually take effect, and renders each node through the
// page's own `nodeTypes.workflow` component so labels/handles are exercised. The
// stubbed <ReactFlow> exposes click surfaces for onNodeClick / onPaneClick /
// onConnect so the inspector and edge logic are reachable from a unit test.
vi.mock('@xyflow/react', async () => {
  const React = await import('react');
  const useNodesState = (initial: unknown[]) => {
    const [nodes, setNodes] = React.useState(initial);
    return [nodes, setNodes, () => {}];
  };
  const useEdgesState = (initial: unknown[]) => {
    const [edges, setEdges] = React.useState(initial);
    return [edges, setEdges, () => {}];
  };
  const addEdge = (edge: Record<string, unknown>, eds: unknown[]) => [
    ...eds,
    { id: edge.id ?? `e-${edge.source}-${edge.target}`, ...edge },
  ];
  const ReactFlow = ({
    nodes = [],
    onNodeClick,
    onPaneClick,
    onConnect,
    nodeTypes,
    children,
  }: Record<string, unknown> & { nodes?: any[]; children?: React.ReactNode }) => {
    const NodeComp = (nodeTypes as Record<string, React.ComponentType<any>> | undefined)?.workflow;
    return (
      <div data-testid="react-flow">
        <button data-testid="rf-pane" onClick={() => (onPaneClick as (() => void) | undefined)?.()}>
          pane
        </button>
        <button
          data-testid="rf-connect"
          onClick={() =>
            (onConnect as ((c: unknown) => void) | undefined)?.({
              source: nodes[0]?.id,
              target: nodes[1]?.id,
            })
          }
        >
          connect
        </button>
        {children as React.ReactNode}
        {nodes.map((n) => (
          <div
            key={n.id}
            data-testid={`rf-node-${n.id}`}
            onClick={(e) => (onNodeClick as ((e: unknown, n: unknown) => void) | undefined)?.(e, n)}
          >
            {NodeComp ? <NodeComp data={n.data} selected={n.selected} /> : null}
          </div>
        ))}
      </div>
    );
  };
  return {
    ReactFlow,
    Background: () => null,
    BackgroundVariant: { Dots: 'dots', Lines: 'lines', Cross: 'cross' },
    Controls: () => null,
    MiniMap: () => null,
    ReactFlowProvider: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
    addEdge,
    useNodesState,
    useEdgesState,
    useReactFlow: () => ({
      screenToFlowPosition: ({ x, y }: { x: number; y: number }) => ({ x, y }),
      fitView: vi.fn(),
    }),
    ConnectionMode: { Strict: 'strict', Loose: 'loose' },
    MarkerType: { ArrowClosed: 'arrowclosed' },
    Handle: () => null,
    Position: { Top: 'top', Bottom: 'bottom', Left: 'left', Right: 'right' },
  };
});

const toastSpy = vi.fn();
vi.mock('@/stores/toast', () => ({ toast: (...a: unknown[]) => toastSpy(...a) }));

import { WorkflowBuilderPage } from './WorkflowBuilderPage';

interface Plan {
  list?: unknown;
  get?: unknown;
  create?: unknown;
  generate?: unknown;
  run?: unknown;
}

function mockFetch(plan: Plan = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    const ok = (body: unknown) =>
      new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

    if (url.includes('/rpa/tools')) return ok([]);
    if (url.includes('/workflows/generate') && method === 'POST')
      return ok(plan.generate ?? { nodes: [], edges: [] });
    if (/\/workflows\/[^/]+\/run/.test(url) && method === 'POST')
      return ok(plan.run ?? { status: 'complete' });
    if (/\/workflows\/[^/?]+$/.test(url) && method === 'GET') return ok(plan.get ?? {});
    if (/\/workflows\/[^/?]+$/.test(url) && method === 'PUT') return ok({});
    if (url.endsWith('/workflows') && method === 'POST') return ok(plan.create ?? { id: 'wf-new' });
    if (url.includes('/workflows') && method === 'GET') return ok(plan.list ?? []);
    return ok({});
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><WorkflowBuilderPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Add a palette node then select the first-created node (node_1). */
function addNode(paletteLabel: string) {
  fireEvent.click(screen.getByRole('button', { name: new RegExp(`Add ${paletteLabel} node`, 'i') }));
}
function selectFirstNode() {
  fireEvent.click(screen.getByTestId('rf-node-node_1'));
}

beforeEach(() => {
  toastSpy.mockReset();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('WorkflowBuilderPage — canvas + palette', () => {
  test('renders the empty-canvas hint and the inspector placeholder', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByText('Build your workflow')).toBeInTheDocument();
    expect(screen.getByText(/Click a node to inspect and configure it/i)).toBeInTheDocument();
    expect(screen.getByText('Node Palette')).toBeInTheDocument();
  });

  test('clicking a palette item adds a node and clears the empty state', async () => {
    mockFetch();
    renderPage();
    addNode('Trigger / Start');
    // The canvas now has node_1 and the empty hint is gone.
    expect(screen.getByTestId('rf-node-node_1')).toBeInTheDocument();
    expect(screen.queryByText('Build your workflow')).not.toBeInTheDocument();
  });

  test('selecting a node opens the inspector; the pane click deselects it', async () => {
    mockFetch();
    renderPage();
    addNode('Agent Step');
    selectFirstNode();
    expect(screen.getByText(/Inspector — agent_step/i)).toBeInTheDocument();
    expect(screen.getByText(/Node ID:/)).toBeInTheDocument();
    // Deselect via the canvas pane.
    fireEvent.click(screen.getByTestId('rf-pane'));
    expect(screen.getByText(/Click a node to inspect and configure it/i)).toBeInTheDocument();
  });

  test('editing the label in the inspector updates the node on the canvas', async () => {
    mockFetch();
    renderPage();
    addNode('Tool Call');
    selectFirstNode();
    const labelInput = screen.getByLabelText('Label') as HTMLInputElement;
    expect(labelInput.value).toBe('Tool Call');
    fireEvent.change(labelInput, { target: { value: 'Fetch Data' } });
    // The rendered canvas node reflects the new label.
    const node = screen.getByTestId('rf-node-node_1');
    expect(node).toHaveTextContent('Fetch Data');
  });

  test('the inspector Delete Node button removes the node', async () => {
    mockFetch();
    renderPage();
    addNode('Trigger / Start');
    selectFirstNode();
    fireEvent.click(screen.getByRole('button', { name: /Delete selected node/i }));
    expect(screen.queryByTestId('rf-node-node_1')).not.toBeInTheDocument();
    expect(screen.getByText('Build your workflow')).toBeInTheDocument();
  });
});

describe('WorkflowBuilderPage — type-specific inspector', () => {
  test('trigger node reveals the CRON field once the type is set to cron', async () => {
    mockFetch();
    renderPage();
    addNode('Trigger / Start');
    selectFirstNode();
    const typeSelect = screen.getByLabelText('Trigger Type');
    expect(screen.queryByLabelText('CRON Expression')).not.toBeInTheDocument();
    fireEvent.change(typeSelect, { target: { value: 'cron' } });
    expect(screen.getByLabelText('CRON Expression')).toBeInTheDocument();
  });

  test('tool_call node shows tool selector, output var and input mapping', async () => {
    mockFetch();
    renderPage();
    addNode('Tool Call');
    selectFirstNode();
    expect(screen.getByLabelText('Tool selector')).toBeInTheDocument();
    expect(screen.getByLabelText('Output Variable')).toBeInTheDocument();
    expect(screen.getByLabelText('Input Mapping (JSON)')).toBeInTheDocument();
  });

  test('decision node shows condition + branch label fields', async () => {
    mockFetch();
    renderPage();
    addNode('Decision / Branch');
    selectFirstNode();
    expect(screen.getByLabelText('Condition Expression')).toBeInTheDocument();
    expect(screen.getByLabelText('True Branch Label')).toBeInTheDocument();
    expect(screen.getByLabelText('False Branch Label')).toBeInTheDocument();
  });

  test('parallel node shows the max-concurrency field and accepts input', async () => {
    mockFetch();
    renderPage();
    addNode('Parallel Fan-out');
    selectFirstNode();
    const maxConc = screen.getByLabelText('Max Concurrency') as HTMLInputElement;
    expect(maxConc).toBeInTheDocument();
    fireEvent.change(maxConc, { target: { value: '8' } });
    expect(maxConc.value).toBe('8');
  });

  test('loop node shows iterator, max-iterations and break condition', async () => {
    mockFetch();
    renderPage();
    addNode('Loop / Map');
    selectFirstNode();
    expect(screen.getByLabelText('Iterator Expression')).toBeInTheDocument();
    expect(screen.getByLabelText('Max Iterations')).toBeInTheDocument();
    expect(screen.getByLabelText('Break Condition')).toBeInTheDocument();
  });

  test('human_approval node shows message, approvers and timeout', async () => {
    mockFetch();
    renderPage();
    addNode('Human Approval');
    selectFirstNode();
    expect(screen.getByLabelText('Approval Message')).toBeInTheDocument();
    expect(screen.getByLabelText('Approvers (comma-separated emails)')).toBeInTheDocument();
    expect(screen.getByLabelText('Timeout (minutes)')).toBeInTheDocument();
  });

  test('delay node shows duration + unit selector', async () => {
    mockFetch();
    renderPage();
    addNode('Delay / Wait');
    selectFirstNode();
    expect(screen.getByLabelText('Duration')).toBeInTheDocument();
    const unit = screen.getByLabelText('Unit') as HTMLSelectElement;
    fireEvent.change(unit, { target: { value: 'hours' } });
    expect(unit.value).toBe('hours');
  });

  test('rag node shows collection, query, strategy and top-k', async () => {
    mockFetch();
    renderPage();
    addNode('RAG Retrieval');
    selectFirstNode();
    expect(screen.getByLabelText('Collection ID')).toBeInTheDocument();
    expect(screen.getByLabelText('Query Template')).toBeInTheDocument();
    expect(screen.getByLabelText('Strategy')).toBeInTheDocument();
    expect(screen.getByLabelText('Top K')).toBeInTheDocument();
  });

  test('skill node shows skill id + goal fields', async () => {
    mockFetch();
    renderPage();
    addNode('Skill');
    selectFirstNode();
    expect(screen.getByLabelText('Skill ID (optional)')).toBeInTheDocument();
    expect(screen.getByLabelText('Goal (for auto-select)')).toBeInTheDocument();
  });

  test('end node shows the output mapping field', async () => {
    mockFetch();
    renderPage();
    addNode('End');
    selectFirstNode();
    expect(screen.getByLabelText('Output Mapping (JSON)')).toBeInTheDocument();
  });
});

describe('WorkflowBuilderPage — templates', () => {
  test('opening the templates modal lists all starter templates', async () => {
    mockFetch();
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /Open templates/i }));
    const dialog = screen.getByRole('dialog', { name: /Workflow templates/i });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByText('Incident Response')).toBeInTheDocument();
    expect(screen.getByText('Employee Onboarding')).toBeInTheDocument();
    expect(screen.getByText('Cost Alert')).toBeInTheDocument();
  });

  test('loading a template populates the canvas and closes the modal', async () => {
    mockFetch();
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /Open templates/i }));
    fireEvent.click(screen.getByRole('button', { name: /Load template Incident Response/i }));
    // Template node ids land on the canvas.
    expect(screen.getByTestId('rf-node-trigger')).toBeInTheDocument();
    expect(screen.getByTestId('rf-node-end')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(toastSpy).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'success', message: expect.stringMatching(/Loaded template: Incident Response/) }),
    );
  });

  test('closing the templates modal with the ✕ button hides it', async () => {
    mockFetch();
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /Open templates/i }));
    fireEvent.click(screen.getByRole('button', { name: /Close templates modal/i }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('WorkflowBuilderPage — validation', () => {
  test('validating an empty canvas surfaces the missing-trigger error banner', async () => {
    mockFetch();
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /Validate workflow/i }));
    expect(screen.getByText(/Workflow must have at least one Trigger node/i)).toBeInTheDocument();
    // The banner can be dismissed.
    fireEvent.click(screen.getByText('✕'));
    expect(screen.queryByText(/Workflow must have at least one Trigger node/i)).not.toBeInTheDocument();
  });

  test('validating a workflow with a trigger reports it valid', async () => {
    mockFetch();
    renderPage();
    addNode('Trigger / Start');
    fireEvent.click(screen.getByRole('button', { name: /Validate workflow/i }));
    expect(toastSpy).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'success', message: expect.stringMatching(/valid/i) }),
    );
  });

  test('an isolated second node is reported by validation', async () => {
    mockFetch();
    renderPage();
    addNode('Trigger / Start');
    addNode('Tool Call');
    fireEvent.click(screen.getByRole('button', { name: /Validate workflow/i }));
    expect(screen.getByText(/node\(s\) are isolated/i)).toBeInTheDocument();
  });
});

describe('WorkflowBuilderPage — save / generate / run', () => {
  test('saving a valid new workflow POSTs to /workflows', async () => {
    const spy = mockFetch({ create: { id: 'wf-created' } });
    renderPage();
    addNode('Trigger / Start');
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Save workflow/i }));
    });
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => String(u).endsWith('/workflows') && (i as RequestInit)?.method === 'POST'),
      ).toBe(true),
    );
    expect(toastSpy).toHaveBeenCalledWith(expect.objectContaining({ message: 'Workflow saved' }));
  });

  test('saving an invalid workflow shows the validation banner and skips the POST', async () => {
    const spy = mockFetch();
    renderPage();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Save workflow/i }));
    });
    expect(screen.getByText(/Workflow must have at least one Trigger node/i)).toBeInTheDocument();
    expect(spy.mock.calls.some(([u, i]) => String(u).endsWith('/workflows') && (i as RequestInit)?.method === 'POST')).toBe(false);
  });

  test('NL generation POSTs to /workflows/generate and populates the canvas', async () => {
    mockFetch({
      generate: {
        nodes: [
          { id: 'g-trigger', type: 'trigger', label: 'Start', position: { x: 0, y: 0 } },
          { id: 'g-end', type: 'end', label: 'Done', position: { x: 0, y: 120 } },
        ],
        edges: [{ id: 'ge1', source: 'g-trigger', target: 'g-end' }],
      },
    });
    const spy = vi.mocked(globalThis.fetch);
    renderPage();
    const textarea = screen.getByLabelText(/Natural language workflow description/i);
    await act(async () => {
      fireEvent.change(textarea, { target: { value: 'Send a daily report' } });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Generate workflow from natural language/i }));
    });
    await waitFor(() => expect(screen.getByTestId('rf-node-g-trigger')).toBeInTheDocument());
    expect(screen.getByTestId('rf-node-g-end')).toBeInTheDocument();
    expect(spy.mock.calls.some(([u]) => String(u).includes('/workflows/generate'))).toBe(true);
    expect(toastSpy).toHaveBeenCalledWith(expect.objectContaining({ message: expect.stringMatching(/Generated 2 nodes/i) }));
  });

  test('NL generation with no nodes toasts an actionable error', async () => {
    mockFetch({ generate: { nodes: [], edges: [] } });
    renderPage();
    const textarea = screen.getByLabelText(/Natural language workflow description/i);
    await act(async () => {
      fireEvent.change(textarea, { target: { value: 'nonsense' } });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Generate workflow from natural language/i }));
    });
    await waitFor(() =>
      expect(toastSpy).toHaveBeenCalledWith(
        expect.objectContaining({ kind: 'error', message: expect.stringMatching(/No nodes returned/i) }),
      ),
    );
  });

  test('running an unsaved workflow saves first then asks the user to run again', async () => {
    const spy = mockFetch({ create: { id: 'wf-created' } });
    renderPage();
    addNode('Trigger / Start');
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '▶ Run' }));
    });
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => String(u).endsWith('/workflows') && (i as RequestInit)?.method === 'POST'),
      ).toBe(true),
    );
    expect(toastSpy).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'info', message: expect.stringMatching(/click Run again/i) }),
    );
  });

  test('loading a saved workflow then running it POSTs to /run and shows per-node output', async () => {
    mockFetch({
      list: [{ id: 'wf-1', name: 'Saved WF', status: 'draft' }],
      get: {
        id: 'wf-1',
        name: 'Saved WF',
        definition: {
          steps: [{ id: 'trigger', type: 'trigger', label: 'Start', position: { x: 0, y: 0 } }],
          edges: [],
        },
      },
      run: {
        status: 'complete',
        node_results: [
          { node_id: 'trigger', status: 'success', input: { seed: 1 }, output: { ok: true }, duration_ms: 42 },
        ],
      },
    });
    const spy = vi.mocked(globalThis.fetch);
    renderPage();
    // Load via the "Load saved…" dropdown (only appears when the list is non-empty).
    const loadSelect = await screen.findByLabelText('Load saved workflow');
    await act(async () => {
      fireEvent.change(loadSelect, { target: { value: 'wf-1' } });
    });
    await waitFor(() => expect(screen.getByTestId('rf-node-trigger')).toBeInTheDocument());

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '▶ Run' }));
    });
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => /\/workflows\/wf-1\/run/.test(String(u)) && (i as RequestInit)?.method === 'POST')).toBe(true),
    );
    // Run output panel renders the JSON payload.
    await waitFor(() => expect(screen.getByText('Run Output')).toBeInTheDocument());
    // Selecting the node shows its per-node last-run output.
    fireEvent.click(screen.getByTestId('rf-node-trigger'));
    expect(screen.getByText('Last Run Output')).toBeInTheDocument();
    expect(screen.getAllByText(/success/i).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: /Export node data/i })).toBeInTheDocument();
  });

  test('Dry Run on a saved workflow hits the dry-run endpoint', async () => {
    mockFetch({
      list: [{ id: 'wf-1', name: 'Saved WF', status: 'draft' }],
      get: {
        id: 'wf-1',
        name: 'Saved WF',
        definition: { steps: [{ id: 'trigger', type: 'trigger', label: 'Start' }], edges: [] },
      },
      run: { status: 'complete', steps: [] },
    });
    const spy = vi.mocked(globalThis.fetch);
    renderPage();
    const loadSelect = await screen.findByLabelText('Load saved workflow');
    await act(async () => {
      fireEvent.change(loadSelect, { target: { value: 'wf-1' } });
    });
    await waitFor(() => expect(screen.getByTestId('rf-node-trigger')).toBeInTheDocument());
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Dry Run/i }));
    });
    await waitFor(() =>
      expect(spy.mock.calls.some(([u]) => String(u).includes('dry_run=true'))).toBe(true),
    );
  });
});

describe('WorkflowBuilderPage — history, clipboard, toolbar', () => {
  test('undo removes an added node and redo restores it', async () => {
    mockFetch();
    renderPage();
    addNode('Trigger / Start');
    expect(screen.getByTestId('rf-node-node_1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Undo/i }));
    expect(screen.queryByTestId('rf-node-node_1')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Redo/i }));
    expect(screen.getByTestId('rf-node-node_1')).toBeInTheDocument();
  });

  test('copying a selected node then pasting adds a duplicate', async () => {
    mockFetch();
    renderPage();
    addNode('Trigger / Start');
    selectFirstNode();
    fireEvent.keyDown(window, { key: 'c', ctrlKey: true });
    expect(toastSpy).toHaveBeenCalledWith(expect.objectContaining({ message: expect.stringMatching(/Copied/i) }));
    fireEvent.keyDown(window, { key: 'v', ctrlKey: true });
    expect(toastSpy).toHaveBeenCalledWith(expect.objectContaining({ message: expect.stringMatching(/Pasted/i) }));
    // The pasted duplicate uses the next counter id.
    expect(screen.getByTestId('rf-node-node_2')).toBeInTheDocument();
  });

  test('select-all then Delete clears the canvas', async () => {
    mockFetch();
    renderPage();
    addNode('Trigger / Start');
    fireEvent.keyDown(window, { key: 'a', ctrlKey: true });
    fireEvent.keyDown(window, { key: 'Delete' });
    expect(screen.queryByTestId('rf-node-node_1')).not.toBeInTheDocument();
    expect(screen.getByText('Build your workflow')).toBeInTheDocument();
  });

  test('the New button resets the canvas and name', async () => {
    mockFetch();
    renderPage();
    addNode('Trigger / Start');
    const nameInput = screen.getByLabelText('Workflow name') as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: 'Renamed' } });
    fireEvent.click(screen.getByRole('button', { name: 'New' }));
    expect((screen.getByLabelText('Workflow name') as HTMLInputElement).value).toBe('My Workflow');
    expect(screen.getByText('Build your workflow')).toBeInTheDocument();
  });

  test('connecting two nodes creates an edge (clears the isolated-node warning)', async () => {
    mockFetch();
    renderPage();
    addNode('Trigger / Start');
    addNode('End');
    // Before connecting, the End node is isolated.
    fireEvent.click(screen.getByRole('button', { name: /Validate workflow/i }));
    expect(screen.getByText(/node\(s\) are isolated/i)).toBeInTheDocument();
    // Connect node_1 -> node_2 through the stubbed ReactFlow surface.
    fireEvent.click(screen.getByTestId('rf-connect'));
    fireEvent.click(screen.getByRole('button', { name: /Validate workflow/i }));
    expect(screen.queryByText(/node\(s\) are isolated/i)).not.toBeInTheDocument();
  });
});
