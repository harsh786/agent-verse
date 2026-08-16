/**
 * Tests for WorkflowRunsPage — run history list with filtering.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import WorkflowRunsPage from '../WorkflowRunsPage';

vi.mock('../../../lib/api/client', () => ({
  workflowEngineApi: {
    get: vi.fn(),
    listRuns: vi.fn(),
    trigger: vi.fn(),
  },
}));

import { workflowEngineApi } from '../../../lib/api/client';

const mockWf = {
  id: 'wf-1', name: 'KYC Workflow', status: 'published',
  version: '1', labels: {}, description: '', created_at: '', updated_at: '',
};

const mockRuns = [
  {
    run_id: 'run-1', workflow_id: 'wf-1', workflow_name: 'KYC Workflow',
    status: 'complete', inputs: {}, outputs: {},
    started_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:01:00Z',
    duration_ms: 60000, step_count: 3, cost_usd: 0.01,
  },
  {
    run_id: 'run-2', workflow_id: 'wf-1', workflow_name: 'KYC Workflow',
    status: 'failed', inputs: {}, outputs: {},
    started_at: '2026-01-01T01:00:00Z', finished_at: null,
    duration_ms: null, step_count: 2, cost_usd: 0.005,
  },
];

function wrap(wfId = 'wf-1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/workflows/${wfId}/runs`]}>
        <Routes>
          <Route path="/workflows/:id/runs" element={<WorkflowRunsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('WorkflowRunsPage', () => {
  beforeEach(() => {
    vi.mocked(workflowEngineApi.get).mockResolvedValue(mockWf as any);
    vi.mocked(workflowEngineApi.listRuns).mockResolvedValue({
      items: mockRuns as any,
      total: 2, page: 1, per_page: 50,
    });
  });

  it('renders runs heading with workflow name', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByText(/KYC Workflow/)).toBeInTheDocument();
    });
  });

  it('shows total run count', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByText(/2 total runs/i)).toBeInTheDocument();
    });
  });

  it('shows run IDs', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByText(/run-1/)).toBeInTheDocument();
      expect(screen.getByText(/run-2/)).toBeInTheDocument();
    });
  });

  it('shows status badges', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByText('complete')).toBeInTheDocument();
      expect(screen.getByText('failed')).toBeInTheDocument();
    });
  });

  it('shows links to run detail pages', async () => {
    wrap();
    await waitFor(() => {
      const links = screen.getAllByRole('link');
      // Should include links to run detail pages
      const runLinks = links.filter(l =>
        l.getAttribute('href')?.includes('workflow-runs/') ||
        l.getAttribute('aria-label')?.includes('run-1')
      );
      expect(runLinks.length).toBeGreaterThan(0);
    });
  });

  it('shows empty state when no runs exist', async () => {
    vi.mocked(workflowEngineApi.listRuns).mockResolvedValue({
      items: [], total: 0, page: 1, per_page: 50,
    });
    wrap();
    await waitFor(() => {
      expect(screen.getByText(/no runs yet/i)).toBeInTheDocument();
    });
  });

  it('shows back link to builder', async () => {
    wrap();
    const link = screen.getAllByRole('link').find(l =>
      l.getAttribute('aria-label')?.includes('back') ||
      l.getAttribute('href')?.includes('/edit')
    );
    expect(link).toBeDefined();
  });

  it('shows duration for completed runs', async () => {
    wrap();
    await waitFor(() => {
      // 60000ms = 60s
      expect(screen.getByText(/60\.0s|60s|1m/)).toBeInTheDocument();
    });
  });

  it('shows cost for runs', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByText('$0.0100')).toBeInTheDocument();
    });
  });
});
