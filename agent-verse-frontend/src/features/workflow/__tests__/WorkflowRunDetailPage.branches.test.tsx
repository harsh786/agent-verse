/**
 * Companion suite to WorkflowRunDetailPage.test.tsx — targets branches and
 * functions not covered there: pause/resume/cancel control handlers (and the
 * refreshRun callback they share), the paused-run Resume button, duration
 * "—" fallback when timestamps are missing, tokens_used defaulting to 0,
 * the empty step-timeline message, and StepResultRow's expand/collapse
 * toggle (including its input/output/error/empty-detail branches).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
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

const baseRun = {
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
  tokens_used: 500,
};

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

describe('WorkflowRunDetailPage — extra branches', () => {
  beforeEach(() => {
    vi.mocked(workflowEngineApi.getRunSteps).mockResolvedValue([]);
    vi.mocked(workflowEngineApi.cancelRun).mockResolvedValue({ run_id: 'run-1', status: 'cancelled' } as any);
    vi.mocked(workflowEngineApi.pauseRun).mockResolvedValue({ run_id: 'run-1', status: 'paused' } as any);
    vi.mocked(workflowEngineApi.resumeRun).mockResolvedValue({ run_id: 'run-1', status: 'running' } as any);
  });

  it('shows Resume button for a paused run and invokes resumeRun on click', async () => {
    vi.mocked(workflowEngineApi.getRun).mockResolvedValue({ ...baseRun, status: 'paused' } as any);
    wrap();

    const resumeBtn = await screen.findByRole('button', { name: /resume run/i });
    fireEvent.click(resumeBtn);

    await waitFor(() => {
      expect(workflowEngineApi.resumeRun).toHaveBeenCalledWith('run-1');
    });
  });

  it('shows Pause button for a running run and invokes pauseRun on click', async () => {
    vi.mocked(workflowEngineApi.getRun).mockResolvedValue({ ...baseRun, status: 'running' } as any);
    wrap();

    const pauseBtn = await screen.findByRole('button', { name: /pause run/i });
    fireEvent.click(pauseBtn);

    await waitFor(() => {
      expect(workflowEngineApi.pauseRun).toHaveBeenCalledWith('run-1');
    });
  });

  it('invokes cancelRun when Stop is clicked on a running run', async () => {
    vi.mocked(workflowEngineApi.getRun).mockResolvedValue({ ...baseRun, status: 'running' } as any);
    wrap();

    const stopBtn = await screen.findByRole('button', { name: /stop run/i });
    fireEvent.click(stopBtn);

    await waitFor(() => {
      expect(workflowEngineApi.cancelRun).toHaveBeenCalledWith('run-1');
    });
  });

  it('shows pending-run controls (pause + stop, no resume)', async () => {
    vi.mocked(workflowEngineApi.getRun).mockResolvedValue({ ...baseRun, status: 'pending' } as any);
    wrap();

    await screen.findByRole('button', { name: /pause run/i });
    expect(screen.getByRole('button', { name: /stop run/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /resume run/i })).toBeNull();
  });

  it('shows "—" for duration when timestamps are missing', async () => {
    vi.mocked(workflowEngineApi.getRun).mockResolvedValue({
      ...baseRun, started_at: null, finished_at: null,
    } as any);
    wrap();

    await waitFor(() => {
      expect(screen.getByText('—')).toBeInTheDocument();
    });
  });

  it('defaults tokens_used to 0 when not provided', async () => {
    const { tokens_used, ...rest } = baseRun;
    vi.mocked(workflowEngineApi.getRun).mockResolvedValue(rest as any);
    wrap();

    await waitFor(() => {
      expect(screen.getByText('0')).toBeInTheDocument();
    });
  });

  it('shows "No step results yet." when steps list is empty', async () => {
    vi.mocked(workflowEngineApi.getRun).mockResolvedValue(baseRun as any);
    wrap();

    await waitFor(() => {
      expect(screen.getByText('No step results yet.')).toBeInTheDocument();
    });
  });

  it('does not render an Inputs section when run.inputs is empty', async () => {
    vi.mocked(workflowEngineApi.getRun).mockResolvedValue({ ...baseRun, inputs: {} } as any);
    wrap();

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /run detail/i })).toBeInTheDocument();
    });
    expect(screen.queryByRole('region', { name: /inputs/i })).toBeNull();
  });

  it('does not render an Outputs section when run.outputs is empty', async () => {
    vi.mocked(workflowEngineApi.getRun).mockResolvedValue({ ...baseRun, outputs: {} } as any);
    wrap();

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /run detail/i })).toBeInTheDocument();
    });
    expect(screen.queryByRole('region', { name: /outputs/i })).toBeNull();
  });

  it('renders "Run not found." when the run query resolves to no data', async () => {
    vi.mocked(workflowEngineApi.getRun).mockResolvedValue(null as any);
    wrap();

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/run not found/i);
    });
  });

  describe('StepResultRow expand/collapse', () => {
    const stepsWithVariety = [
      {
        step_id: 'step-with-io',
        step_type: 'tool',
        status: 'complete',
        input: { foo: 'bar' },
        output: { result: 'ok' },
        error: null,
        duration_ms: 250,
      },
      {
        step_id: 'step-failed',
        step_type: 'llm',
        status: 'failed',
        input: null,
        output: null,
        error: 'boom',
        duration_ms: 1500,
      },
      {
        step_id: 'step-empty',
        step_type: 'tool',
        status: 'running',
        input: null,
        output: null,
        error: null,
        duration_ms: null,
      },
    ];

    beforeEach(() => {
      vi.mocked(workflowEngineApi.getRun).mockResolvedValue(baseRun as any);
      vi.mocked(workflowEngineApi.getRunSteps).mockResolvedValue(stepsWithVariety as any);
    });

    it('expands a step to show input/output (sub-second duration) and collapses again', async () => {
      wrap();

      const toggle = await screen.findByRole('button', { name: /step step-with-io — complete/i });
      expect(toggle).toHaveAttribute('aria-expanded', 'false');
      expect(screen.getByText('250ms')).toBeInTheDocument();

      fireEvent.click(toggle);
      expect(toggle).toHaveAttribute('aria-expanded', 'true');
      expect(screen.getByText(/"foo": "bar"/)).toBeInTheDocument();
      expect(screen.getByText(/"result": "ok"/)).toBeInTheDocument();

      fireEvent.click(toggle);
      expect(toggle).toHaveAttribute('aria-expanded', 'false');
      expect(screen.queryByText(/"foo": "bar"/)).toBeNull();
    });

    it('expands a failed step to show its error and multi-second duration', async () => {
      wrap();

      const toggle = await screen.findByRole('button', { name: /step step-failed — failed/i });
      expect(screen.getByText('1.5s')).toBeInTheDocument();

      fireEvent.click(toggle);
      expect(screen.getByText('boom')).toBeInTheDocument();
    });

    it('shows the empty-detail message for a step with no input/output/error', async () => {
      wrap();

      const toggle = await screen.findByRole('button', { name: /step step-empty — running/i });
      fireEvent.click(toggle);

      expect(screen.getByText('No input or output recorded for this step.')).toBeInTheDocument();
    });
  });
});
