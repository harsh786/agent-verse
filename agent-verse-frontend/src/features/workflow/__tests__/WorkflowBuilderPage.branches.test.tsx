/**
 * Companion suite to WorkflowBuilderPage.test.tsx — targets branches not
 * covered there: the YAML editor (open/edit/close, valid + invalid save),
 * save-from-canvas, drag-and-drop onto the canvas, keyboard shortcuts
 * (undo/redo/duplicate/delete, wired via useCanvasKeyboardShortcuts which
 * listens on `document`), node selection opening the config panel (and its
 * onUpdate/onClose callbacks), the test-run success + failure paths, and the
 * error-state Retry / Back-to-list buttons.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { ReactNode } from 'react';
import WorkflowBuilderPage from '../WorkflowBuilderPage';

// Captured props from the mocked <ReactFlow> so tests can invoke its
// callbacks directly (onConnect/onNodeClick/onPaneClick/onNodesChange), since
// the mock itself renders a static div rather than a real canvas.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let capturedRFProps: any = {};

vi.mock('@xyflow/react', async () => {
  const actual = await vi.importActual('@xyflow/react');
  return {
    ...actual,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ReactFlow: (props: any) => {
      capturedRFProps = props;
      return (
        <div data-testid="react-flow-canvas" role="main" aria-label="Workflow canvas">
          {props.children}
        </div>
      );
    },
    ReactFlowProvider: ({ children }: { children: ReactNode }) => <div>{children}</div>,
    Background: () => null,
    Controls: () => <div data-testid="canvas-controls" aria-label="Canvas controls" />,
    MiniMap: () => <div data-testid="canvas-minimap" aria-label="Workflow minimap" />,
    Panel: ({ children }: { children: ReactNode }) => <div>{children}</div>,
    useReactFlow: () => ({
      screenToFlowPosition: ({ x, y }: { x: number; y: number }) => ({ x, y }),
      getNodes: () => [],
      setNodes: vi.fn(),
      fitView: vi.fn(),
    }),
    useNodesState: () => [[], mockSetNodes, vi.fn()],
    useEdgesState: () => [[], mockSetEdges, vi.fn()],
    addEdge: vi.fn((params: unknown, edges: unknown[]) => [...edges, params]),
    BackgroundVariant: { Dots: 'dots' },
    ConnectionMode: { Loose: 'loose' },
    MarkerType: { ArrowClosed: 'arrowclosed' },
  };
});

// Simplified config-panel mock — exposes buttons so the parent's onUpdate /
// onClose lambdas (defined inline in BuilderCanvas) are directly exercised.
vi.mock('../builder/WorkflowStepConfig', () => ({
  WorkflowStepConfig: ({
    node,
    onUpdate,
    onClose,
  }: {
    node: { id: string };
    onUpdate: (u: Record<string, unknown>) => void;
    onClose: () => void;
  }) => (
    <div data-testid="step-config-panel">
      <span>Configuring {node.id}</span>
      <button onClick={() => onUpdate({ label: 'Updated label' })}>Apply update</button>
      <button onClick={onClose}>Close panel</button>
    </div>
  ),
}));

vi.mock('../../../lib/api/client', () => ({
  workflowEngineApi: {
    get: vi.fn(),
    update: vi.fn(),
    publish: vi.fn(),
    trigger: vi.fn(),
  },
}));

import { workflowEngineApi } from '../../../lib/api/client';

const mockSetNodes = vi.fn();
const mockSetEdges = vi.fn();

const mockWf = {
  id: 'wf-1',
  name: 'My Test Workflow',
  description: 'A workflow for testing',
  status: 'draft',
  version: '1',
  labels: {},
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  definition: { name: 'My Test Workflow', steps: [] },
};

function wrap(wfId = 'wf-1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/workflows/${wfId}/edit`]}>
        <Routes>
          <Route path="/workflows/:id/edit" element={<WorkflowBuilderPage />} />
          <Route path="/workflows" element={<div>Workflow list page</div>} />
          <Route path="/workflows/:id/runs/:runId" element={<div>Run detail page</div>} />
          <Route path="/workflows/:id/runs" element={<div>Runs list page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  capturedRFProps = {};
  mockSetNodes.mockClear();
  mockSetEdges.mockClear();
  vi.mocked(workflowEngineApi.get).mockResolvedValue(mockWf as never);
  vi.mocked(workflowEngineApi.update).mockResolvedValue(mockWf as never);
  vi.mocked(workflowEngineApi.publish).mockResolvedValue({ ...mockWf, status: 'published' } as never);
  vi.mocked(workflowEngineApi.trigger).mockResolvedValue({
    run_id: 'run-1', workflow_id: 'wf-1', status: 'pending',
    inputs: {}, outputs: {}, cost_usd: 0, step_count: 0,
  } as never);
});

describe('WorkflowBuilderPage branches', () => {
  it('save button (canvas mode) calls workflowEngineApi.update', async () => {
    wrap();
    await waitFor(() => screen.getByRole('button', { name: /^save workflow$/i }));
    fireEvent.click(screen.getByRole('button', { name: /^save workflow$/i }));
    await waitFor(() => expect(workflowEngineApi.update).toHaveBeenCalled());
    const [, body] = vi.mocked(workflowEngineApi.update).mock.calls[0];
    expect(body).toHaveProperty('definition');
  });

  it('toggling the YAML editor shows and hides the editor panel', async () => {
    wrap();
    await waitFor(() => screen.getByRole('button', { name: /toggle yaml editor/i }));
    fireEvent.click(screen.getByRole('button', { name: /toggle yaml editor/i }));
    expect(await screen.findByLabelText(/edit workflow yaml/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /close yaml editor/i }));
    await waitFor(() =>
      expect(screen.queryByLabelText(/edit workflow yaml/i)).not.toBeInTheDocument()
    );
  });

  it('editing valid YAML then saving parses it and calls update', async () => {
    wrap();
    await waitFor(() => screen.getByRole('button', { name: /toggle yaml editor/i }));
    fireEvent.click(screen.getByRole('button', { name: /toggle yaml editor/i }));
    const textarea = await screen.findByLabelText(/edit workflow yaml/i);
    fireEvent.change(textarea, { target: { value: 'name: Renamed\nsteps: []\n' } });
    fireEvent.click(screen.getByRole('button', { name: /^save workflow$/i }));
    await waitFor(() => expect(workflowEngineApi.update).toHaveBeenCalled());
    const [, body] = vi.mocked(workflowEngineApi.update).mock.calls.at(-1)!;
    expect((body as { definition: { name: string } }).definition.name).toBe('Renamed');
  });

  it('saving with malformed YAML falls back to the empty-steps definition (catch branch)', async () => {
    wrap();
    await waitFor(() => screen.getByRole('button', { name: /toggle yaml editor/i }));
    fireEvent.click(screen.getByRole('button', { name: /toggle yaml editor/i }));
    const textarea = await screen.findByLabelText(/edit workflow yaml/i);
    // Unterminated flow mapping — js-yaml throws a YAMLException for this.
    fireEvent.change(textarea, { target: { value: '{ this: is not, valid' } });
    fireEvent.click(screen.getByRole('button', { name: /^save workflow$/i }));
    await waitFor(() => expect(workflowEngineApi.update).toHaveBeenCalled());
    const [, body] = vi.mocked(workflowEngineApi.update).mock.calls.at(-1)!;
    expect(body).toEqual({ definition: { name: 'My Test Workflow', steps: [] } });
  });

  it('publish button calls the publish endpoint and refreshes the workflow', async () => {
    wrap();
    await waitFor(() => screen.getByRole('button', { name: /publish/i }));
    fireEvent.click(screen.getByRole('button', { name: /publish/i }));
    await waitFor(() => expect(workflowEngineApi.publish).toHaveBeenCalledWith('wf-1'));
  });

  it('test button triggers a run and navigates to the run detail page on success', async () => {
    wrap();
    await waitFor(() => screen.getByRole('button', { name: /test workflow/i }));
    fireEvent.click(screen.getByRole('button', { name: /test workflow/i }));
    // handleSave() runs first as part of the test mutation.
    await waitFor(() => expect(workflowEngineApi.update).toHaveBeenCalled());
    await waitFor(() => expect(workflowEngineApi.trigger).toHaveBeenCalledWith('wf-1', {}));
    expect(await screen.findByText('Run detail page')).toBeInTheDocument();
  });

  it('test button navigates to the runs list when the response has no run id', async () => {
    vi.mocked(workflowEngineApi.trigger).mockResolvedValue({ status: 'pending' } as never);
    wrap();
    await waitFor(() => screen.getByRole('button', { name: /test workflow/i }));
    fireEvent.click(screen.getByRole('button', { name: /test workflow/i }));
    expect(await screen.findByText('Runs list page')).toBeInTheDocument();
  });

  it('test button failure re-enables the button instead of navigating', async () => {
    vi.mocked(workflowEngineApi.trigger).mockRejectedValue(new Error('trigger failed'));
    wrap();
    await waitFor(() => screen.getByRole('button', { name: /test workflow/i }));
    fireEvent.click(screen.getByRole('button', { name: /test workflow/i }));
    await waitFor(() => expect(workflowEngineApi.trigger).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByRole('button', { name: /test workflow/i })).not.toBeDisabled());
    expect(screen.queryByText('Run detail page')).not.toBeInTheDocument();
    expect(screen.queryByText('Runs list page')).not.toBeInTheDocument();
  });

  it('runs button navigates to the runs list for this workflow', async () => {
    wrap();
    await waitFor(() => screen.getByRole('button', { name: /view workflow runs/i }));
    fireEvent.click(screen.getByRole('button', { name: /view workflow runs/i }));
    expect(await screen.findByText('Runs list page')).toBeInTheDocument();
  });

  it('back-chevron navigates to the workflow list', async () => {
    wrap();
    await waitFor(() => screen.getByRole('button', { name: /back to workflows/i }));
    fireEvent.click(screen.getByRole('button', { name: /back to workflows/i }));
    expect(await screen.findByText('Workflow list page')).toBeInTheDocument();
  });

  it('error state: Back to list navigates away, and Retry re-fetches', async () => {
    // The query retries 3x with backoff (up to ~7s) before settling into error.
    vi.mocked(workflowEngineApi.get).mockRejectedValue(new Error('nope'));
    wrap('bad-id');
    await waitFor(() => screen.getByRole('alert'), { timeout: 10000 });
    expect(screen.getByText(/couldn't load this workflow/i)).toBeInTheDocument();
    vi.mocked(workflowEngineApi.get).mockResolvedValue(mockWf as never);
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() => expect(screen.getByText('My Test Workflow')).toBeInTheDocument());
  }, 15000);

  it('error state: Back to list button navigates to the workflow list', async () => {
    vi.mocked(workflowEngineApi.get).mockRejectedValue(new Error('nope'));
    wrap('bad-id');
    await waitFor(() => screen.getByRole('alert'), { timeout: 10000 });
    fireEvent.click(screen.getByRole('button', { name: /back to list/i }));
    expect(await screen.findByText('Workflow list page')).toBeInTheDocument();
  }, 15000);

  it('does not show the publish button once the workflow is published', async () => {
    vi.mocked(workflowEngineApi.get).mockResolvedValue({ ...mockWf, status: 'published' } as never);
    wrap();
    await waitFor(() => screen.getByText('My Test Workflow'));
    expect(screen.queryByRole('button', { name: /^publish workflow$/i })).not.toBeInTheDocument();
  });

  it('dropping a palette tile onto the canvas adds a node (onDrop branch)', async () => {
    const { container } = wrap();
    await waitFor(() => screen.getByTestId('react-flow-canvas'));
    const dropTarget = container.querySelector('.flex-1.relative') as HTMLElement;
    expect(dropTarget).toBeTruthy();
    fireEvent.dragOver(dropTarget, {
      dataTransfer: { dropEffect: '', getData: () => '' },
    });
    fireEvent.drop(dropTarget, {
      clientX: 200,
      clientY: 150,
      dataTransfer: { getData: () => 'llm_call' },
    });
    expect(mockSetNodes).toHaveBeenCalled();
  });

  it('dropping with no step type on the dataTransfer is a no-op', async () => {
    const { container } = wrap();
    await waitFor(() => screen.getByTestId('react-flow-canvas'));
    const dropTarget = container.querySelector('.flex-1.relative') as HTMLElement;
    mockSetNodes.mockClear();
    fireEvent.drop(dropTarget, { dataTransfer: { getData: () => '' } });
    expect(mockSetNodes).not.toHaveBeenCalled();
  });

  it('clicking a node opens the config panel; Apply update and Close exercise the callbacks', async () => {
    wrap();
    await waitFor(() => screen.getByTestId('react-flow-canvas'));
    expect(typeof capturedRFProps.onNodeClick).toBe('function');
    act(() => capturedRFProps.onNodeClick({}, { id: 'step-1', position: { x: 0, y: 0 }, data: {} }));
    expect(await screen.findByTestId('step-config-panel')).toBeInTheDocument();
    expect(screen.getByText('Configuring step-1')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Apply update'));
    // onUpdate re-renders the same panel keyed by node id — still present.
    expect(screen.getByTestId('step-config-panel')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Close panel'));
    await waitFor(() => expect(screen.queryByTestId('step-config-panel')).not.toBeInTheDocument());
  });

  it('clicking the pane closes the config panel (onPaneClick branch)', async () => {
    wrap();
    await waitFor(() => screen.getByTestId('react-flow-canvas'));
    act(() => capturedRFProps.onNodeClick({}, { id: 'step-2', position: { x: 0, y: 0 }, data: {} }));
    await screen.findByTestId('step-config-panel');
    act(() => capturedRFProps.onPaneClick());
    await waitFor(() => expect(screen.queryByTestId('step-config-panel')).not.toBeInTheDocument());
  });

  it('onConnect adds an edge via addEdge/setEdges', async () => {
    wrap();
    await waitFor(() => screen.getByTestId('react-flow-canvas'));
    expect(typeof capturedRFProps.onConnect).toBe('function');
    act(() => capturedRFProps.onConnect({ source: 'a', target: 'b', sourceHandle: null, targetHandle: null }));
    expect(mockSetEdges).toHaveBeenCalled();
  });

  it('keyboard shortcuts: Ctrl+D duplicates and Delete removes selected nodes/edges', async () => {
    wrap();
    await waitFor(() => screen.getByTestId('react-flow-canvas'));
    mockSetNodes.mockClear();
    mockSetEdges.mockClear();

    fireEvent.keyDown(document.body, { key: 'd', ctrlKey: true });
    expect(mockSetNodes).toHaveBeenCalled();

    mockSetNodes.mockClear();
    fireEvent.keyDown(document.body, { key: 'Delete' });
    expect(mockSetNodes).toHaveBeenCalled();
    expect(mockSetEdges).toHaveBeenCalled();
  });

  it('auto-layout button re-lays-out nodes and edges', async () => {
    wrap();
    await waitFor(() => screen.getByRole('button', { name: /auto.layout/i }));
    mockSetNodes.mockClear();
    mockSetEdges.mockClear();
    fireEvent.click(screen.getByRole('button', { name: /auto.layout/i }));
    await waitFor(() => expect(mockSetNodes).toHaveBeenCalled());
    expect(mockSetEdges).toHaveBeenCalled();
  });

  it('undo/redo buttons start disabled (no history has been pushed)', async () => {
    wrap();
    await waitFor(() => screen.getByRole('button', { name: /^undo$/i }));
    expect(screen.getByRole('button', { name: /^undo$/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /^redo$/i })).toBeDisabled();
  });
});
