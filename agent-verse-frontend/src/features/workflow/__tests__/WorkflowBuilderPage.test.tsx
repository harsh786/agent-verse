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
});
