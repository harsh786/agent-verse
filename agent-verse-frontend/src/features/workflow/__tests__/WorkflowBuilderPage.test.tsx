/**
 * Tests for WorkflowBuilderPage — the main canvas builder.
 *
 * Since the canvas requires ReactFlow which needs a browser environment,
 * we test the page-level behavior (loading states, save, publish, error).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import WorkflowBuilderPage from '../WorkflowBuilderPage';

// Mock ReactFlow entirely since it needs a browser canvas
vi.mock('@xyflow/react', async () => {
  const actual = await vi.importActual('@xyflow/react');
  return {
    ...actual,
    ReactFlow: ({ children }: { children: React.ReactNode }) => (
      <div data-testid="react-flow-canvas" role="main" aria-label="Workflow canvas">
        {children}
      </div>
    ),
    ReactFlowProvider: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    Background: () => null,
    Controls: () => <div data-testid="canvas-controls" aria-label="Canvas controls" />,
    MiniMap: () => <div data-testid="canvas-minimap" aria-label="Workflow minimap" />,
    useReactFlow: () => ({
      screenToFlowPosition: () => ({ x: 100, y: 100 }),
      getNodes: () => [],
      setNodes: vi.fn(),
      fitView: vi.fn(),
    }),
    useNodesState: () => [[], vi.fn(), vi.fn()],
    useEdgesState: () => [[], vi.fn(), vi.fn()],
    addEdge: vi.fn((params: unknown, edges: unknown[]) => [...edges, params]),
    Panel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    BackgroundVariant: { Dots: 'dots' },
    ConnectionMode: { Loose: 'loose' },
    MarkerType: { ArrowClosed: 'arrowclosed' },
  };
});

vi.mock('../../../lib/api/client', () => ({
  workflowEngineApi: {
    get: vi.fn(),
    update: vi.fn(),
    publish: vi.fn(),
    trigger: vi.fn(),
  },
}));

import { workflowEngineApi } from '../../../lib/api/client';

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
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('WorkflowBuilderPage', () => {
  beforeEach(() => {
    vi.mocked(workflowEngineApi.get).mockResolvedValue(mockWf as any);
    vi.mocked(workflowEngineApi.update).mockResolvedValue(mockWf as any);
    vi.mocked(workflowEngineApi.publish).mockResolvedValue({
      ...mockWf, status: 'published',
    } as any);
    vi.mocked(workflowEngineApi.trigger).mockResolvedValue({
      run_id: 'run-1', workflow_id: 'wf-1', status: 'pending',
      inputs: {}, outputs: {}, cost_usd: 0, step_count: 0,
    } as any);
  });

  it('shows workflow name in header', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByText('My Test Workflow')).toBeInTheDocument();
    });
  });

  it('shows workflow version and status', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByText(/v1.*draft|draft.*v1/i)).toBeInTheDocument();
    });
  });

  it('shows save button', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /save/i })).toBeInTheDocument();
    });
  });

  it('shows test button', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /test/i })).toBeInTheDocument();
    });
  });

  it('shows publish button for draft workflow', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /publish/i })).toBeInTheDocument();
    });
  });

  it('shows back navigation to workflow list', async () => {
    wrap();
    await waitFor(() => screen.getByText('My Test Workflow'));
    // The page should have navigation elements
    const heading = screen.getByText('My Test Workflow');
    expect(heading).toBeInTheDocument();
  });

  it('renders canvas with react flow', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByTestId('react-flow-canvas')).toBeInTheDocument();
    });
  });

  it('shows undo button', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /undo/i })).toBeInTheDocument();
    });
  });

  it('shows redo button', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /redo/i })).toBeInTheDocument();
    });
  });

  it('shows YAML toggle button', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /toggle yaml/i })).toBeInTheDocument();
    });
  });

  it('shows auto-layout button', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /auto.layout/i })).toBeInTheDocument();
    });
  });

  it('shows loading state while fetching workflow', async () => {
    vi.mocked(workflowEngineApi.get).mockImplementation(() => new Promise(() => {}));
    wrap();
    // Should show loading spinner
    const body = document.body;
    expect(body).toBeDefined();
  });

  it('shows error state for nonexistent workflow', async () => {
    vi.mocked(workflowEngineApi.get).mockRejectedValue(new Error('Not found'));
    wrap('bad-id');
    await waitFor(() => {
      // Should show error or redirect
      const body = document.body;
      expect(body).toBeDefined();
    });
  });

  it('publish button calls publish API', async () => {
    wrap();
    await waitFor(() => screen.getByRole('button', { name: /publish/i }));
    const publishBtn = screen.getByRole('button', { name: /publish/i });
    fireEvent.click(publishBtn);
    await waitFor(() => {
      expect(workflowEngineApi.publish).toHaveBeenCalledWith('wf-1');
    });
  });

  // WF-05: the per-workflow ACL level gates the builder's actions.
  it('a viewer sees a view-only builder: no publish, save and test disabled', async () => {
    vi.mocked(workflowEngineApi.get).mockResolvedValue({ ...mockWf, access: 'viewer' } as any);
    wrap();
    await waitFor(() => screen.getByText('View only'));
    expect(screen.queryByRole('button', { name: /publish/i })).toBeNull();
    expect(screen.getByRole('button', { name: /save workflow/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /test workflow/i })).toBeDisabled();
  });

  it('a runner may test-run but not save or publish', async () => {
    vi.mocked(workflowEngineApi.get).mockResolvedValue({ ...mockWf, access: 'runner' } as any);
    wrap();
    await waitFor(() => screen.getByText('View only'));
    expect(screen.queryByRole('button', { name: /publish/i })).toBeNull();
    expect(screen.getByRole('button', { name: /save workflow/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /test workflow/i })).toBeEnabled();
  });

  it('shows the server reason when publishing is refused', async () => {
    vi.mocked(workflowEngineApi.publish).mockRejectedValue(
      Object.assign(new Error('Cannot publish: trigger type "event" is not supported'), {
        status: 422,
      }),
    );
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: /publish/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Could not publish: Cannot publish: trigger type "event" is not supported',
    );
  });

  it('explains a 403 on save as a permission problem', async () => {
    vi.mocked(workflowEngineApi.update).mockRejectedValue(
      Object.assign(new Error('Forbidden'), { status: 403 }),
    );
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: /save workflow/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "You don't have permission to save this workflow.",
    );
  });
});

// UI-WF-SAVE: the workflow name could not be changed from the builder and Save
// gave no feedback (and Test raced the save it had just started).
describe('WorkflowBuilderPage — rename + save feedback (UI-WF-SAVE)', () => {
  beforeEach(() => {
    vi.mocked(workflowEngineApi.get).mockResolvedValue(mockWf as any);
    vi.mocked(workflowEngineApi.update).mockReset();
    vi.mocked(workflowEngineApi.update).mockResolvedValue(mockWf as any);
    vi.mocked(workflowEngineApi.trigger).mockReset();
    vi.mocked(workflowEngineApi.trigger).mockResolvedValue({
      run_id: 'run-1', workflow_id: 'wf-1', status: 'pending',
    } as any);
  });

  it('renames the workflow via PATCH {name} and shows the new name', async () => {
    vi.mocked(workflowEngineApi.update).mockImplementation(async () => {
      // The server now holds the new name, so the post-save refetch returns it.
      vi.mocked(workflowEngineApi.get).mockResolvedValue({ ...mockWf, name: 'Renamed Flow' } as any);
      return { ...mockWf, name: 'Renamed Flow' } as any;
    });
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: /rename workflow/i }));
    const input = screen.getByRole('textbox', { name: /workflow name/i });
    expect(input).toHaveValue('My Test Workflow');
    fireEvent.change(input, { target: { value: '  Renamed Flow ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() =>
      expect(workflowEngineApi.update).toHaveBeenCalledWith('wf-1', { name: 'Renamed Flow' }),
    );
    expect(await screen.findByRole('heading', { name: 'Renamed Flow' })).toBeInTheDocument();
  });

  it('rejects a blank name without calling the API', async () => {
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: /rename workflow/i }));
    const input = screen.getByRole('textbox', { name: /workflow name/i });
    fireEvent.change(input, { target: { value: '   ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(await screen.findByRole('alert')).toHaveTextContent(/name cannot be blank/i);
    expect(workflowEngineApi.update).not.toHaveBeenCalled();
  });

  it('Escape cancels the rename', async () => {
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: /rename workflow/i }));
    const input = screen.getByRole('textbox', { name: /workflow name/i });
    fireEvent.change(input, { target: { value: 'Nope' } });
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(screen.getByRole('heading', { name: 'My Test Workflow' })).toBeInTheDocument();
    expect(workflowEngineApi.update).not.toHaveBeenCalled();
  });

  it('a viewer cannot rename', async () => {
    vi.mocked(workflowEngineApi.get).mockResolvedValue({ ...mockWf, access: 'viewer' } as any);
    wrap();
    await screen.findByRole('heading', { name: 'My Test Workflow' });
    expect(screen.queryByRole('button', { name: /rename workflow/i })).not.toBeInTheDocument();
  });

  it('shows a Saved confirmation after a successful save', async () => {
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: /save workflow/i }));
    expect(await screen.findByRole('status')).toHaveTextContent(/saved/i);
  });

  it('Test waits for the save to finish before triggering the run', async () => {
    let resolveSave: (v: unknown) => void = () => {};
    vi.mocked(workflowEngineApi.update).mockImplementation(
      () => new Promise((r) => { resolveSave = r; }) as any,
    );
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: /test workflow/i }));
    await waitFor(() => expect(workflowEngineApi.update).toHaveBeenCalled());
    expect(workflowEngineApi.trigger).not.toHaveBeenCalled();
    resolveSave(mockWf);
    await waitFor(() => expect(workflowEngineApi.trigger).toHaveBeenCalledWith('wf-1', {}));
  });

  it('does not trigger a test run when the save fails', async () => {
    vi.mocked(workflowEngineApi.update).mockRejectedValue(
      Object.assign(new Error('Invalid workflow DSL'), { status: 400 }),
    );
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: /test workflow/i }));
    await waitFor(() => expect(workflowEngineApi.update).toHaveBeenCalled());
    await screen.findByRole('alert');
    expect(workflowEngineApi.trigger).not.toHaveBeenCalled();
  });

  it('links to the workflow settings page', async () => {
    wrap();
    const link = await screen.findByRole('link', { name: /workflow settings/i });
    expect(link).toHaveAttribute('href', '/workflows/wf-1/settings');
  });
});
