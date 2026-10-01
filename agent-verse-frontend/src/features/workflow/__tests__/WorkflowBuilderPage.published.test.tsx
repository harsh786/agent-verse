/**
 * WF-30: a PUBLISHED workflow opens read-only in the builder. Its definition can
 * only change through Unpublish -> edit -> Publish (a new recorded version, through
 * publish approval when required); the backend refuses in-place edits with 409.
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
    unpublish: vi.fn(),
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

describe('WorkflowBuilderPage — published workflow', () => {
  beforeEach(() => {
    vi.mocked(workflowEngineApi.get).mockResolvedValue({ ...mockWf, status: 'published' } as any);
    vi.mocked(workflowEngineApi.unpublish).mockResolvedValue(mockWf as any);
  });

  it('opens read-only: save and rename are unavailable', async () => {
    wrap();
    await screen.findByText(/read-only/i);
    expect(screen.getByRole('button', { name: /save workflow/i })).toBeDisabled();
    expect(screen.queryByRole('button', { name: /rename workflow/i })).not.toBeInTheDocument();
  });

  it('offers Unpublish to edit, which unpublishes the workflow', async () => {
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: /unpublish to edit/i }));
    await waitFor(() => expect(workflowEngineApi.unpublish).toHaveBeenCalledWith('wf-1'));
  });

  it('surfaces the 409 detail when the server refuses an edit', async () => {
    vi.mocked(workflowEngineApi.get).mockResolvedValue(mockWf as any);
    const err = Object.assign(new Error('workflow is published; unpublish it first'), {
      status: 409,
    });
    vi.mocked(workflowEngineApi.update).mockRejectedValue(err);
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: /save workflow/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/published.*unpublish/i);
  });
});
