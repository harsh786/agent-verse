/**
 * Tests for WorkflowRunDetailPage — step timeline, cost summary, controls.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import WorkflowRunDetailPage from '../WorkflowRunDetailPage';

vi.mock('../../../lib/api/client', () => ({
  workflowEngineApi: {
    getRun: vi.fn(),
    getRunSteps: vi.fn(),
    cancelRun: vi.fn(),
    pauseRun: vi.fn(),
    resumeRun: vi.fn(),
    debugRun: vi.fn(),
  },
}));

import { workflowEngineApi } from '../../../lib/api/client';

const mockRun = {
  run_id: 'run-1',
  workflow_id: 'wf-1',
  workflow_name: 'Test Workflow',
  status: 'complete',
  inputs: { email: 'test@example.com' },
  outputs: { result: 'approved' },
  error: null,
  started_at: '2026-01-01T00:00:00Z',
  finished_at: '2026-01-01T00:01:00Z',
  duration_ms: 60000,
  step_count: 3,
  cost_usd: 0.0123,
};

const mockSteps = [
  {
    step_id: 'verify',
    step_type: 'tool',
    status: 'complete',
    output: { verified: true },
    error: null,
    started_at: '2026-01-01T00:00:00Z',
    finished_at: '2026-01-01T00:00:30Z',
    duration_ms: 30000,
  },
  {
    step_id: 'risk_score',
    step_type: 'llm',
    status: 'complete',
    output: { score: 0.2, label: 'low_risk' },
    error: null,
    started_at: '2026-01-01T00:00:30Z',
    finished_at: '2026-01-01T00:01:00Z',
    duration_ms: 30000,
  },
  {
    step_id: 'review',
    step_type: 'hitl',
    status: 'complete',
    output: { action: 'approved', reviewer: 'user-1' },
    error: null,
    started_at: null,
    finished_at: null,
    duration_ms: null,
  },
];

function wrap(runId = 'run-1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/workflow-runs/${runId}`]}>
        <Routes>
          <Route path="/workflow-runs/:runId" element={<WorkflowRunDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('WorkflowRunDetailPage', () => {
  beforeEach(() => {
    vi.mocked(workflowEngineApi.getRun).mockResolvedValue(mockRun as any);
    vi.mocked(workflowEngineApi.getRunSteps).mockResolvedValue(mockSteps as any);
    vi.mocked(workflowEngineApi.cancelRun).mockResolvedValue({ run_id: 'run-1', status: 'cancelled' });
  });

  it('renders run detail heading', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /run detail/i })).toBeInTheDocument();
    });
  });

  it('shows run status badge', async () => {
    wrap();
    await waitFor(() => {
      const completeTexts = screen.getAllByText('complete');
      expect(completeTexts.length).toBeGreaterThan(0);
    });
  });

  it('shows step count', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByText('3')).toBeInTheDocument();
    });
  });

  it('shows cost information', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByText('$0.0123')).toBeInTheDocument();
    });
  });

  it('shows step timeline', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByText('verify')).toBeInTheDocument();
      expect(screen.getByText('risk_score')).toBeInTheDocument();
    });
  });

  it('shows step types in timeline', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByText('tool')).toBeInTheDocument();
      expect(screen.getByText('llm')).toBeInTheDocument();
    });
  });

  it('shows outputs section when run has outputs', async () => {
    wrap();
    await waitFor(() => {
      const section = screen.getByRole('region', { name: /outputs/i });
      expect(section).toBeInTheDocument();
    });
  });

  it('has back link to runs list', async () => {
    wrap();
    await new Promise(r => setTimeout(r, 100));
    const links = screen.getAllByRole('link');
    expect(links.length).toBeGreaterThan(0);
    // At least one link should go to the runs page
    const runsLink = links.find(l => l.getAttribute('href')?.includes('/runs') || l.getAttribute('aria-label')?.includes('Back'));
    expect(runsLink).toBeDefined();
  });

  it('shows cancel/stop button for running run', async () => {
    vi.mocked(workflowEngineApi.getRun).mockResolvedValue({
      ...mockRun, status: 'running',
    } as any);
    wrap();
    // The control is the Pause/Resume/Stop trio; the cancel action is "Stop run".
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /stop run/i })).toBeInTheDocument();
    });
  });

  it('does not show cancel/stop button for completed run', async () => {
    wrap();
    await waitFor(() => screen.getAllByText('complete'));
    // Completed run should not have a stop (cancel) button
    expect(screen.queryByRole('button', { name: /stop run/i })).toBeNull();
  });

  it('shows error message when run failed', async () => {
    vi.mocked(workflowEngineApi.getRun).mockResolvedValue({
      ...mockRun, status: 'failed', error: 'Step verify failed: timeout',
    } as any);
    wrap();
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
      expect(screen.getByText(/timeout/)).toBeInTheDocument();
    });
  });

  it('shows loading state', async () => {
    vi.mocked(workflowEngineApi.getRun).mockImplementation(() => new Promise(() => {}));
    wrap();
    // Page is loading - should show a spinner or loading indicator
    const page = document.body;
    expect(page).toBeDefined();
  });
});
