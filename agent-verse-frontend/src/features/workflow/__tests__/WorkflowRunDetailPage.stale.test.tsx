/**
 * UI-APPROVALS-WORKFLOW (RW-11): the run view must not go stale after an
 * approval. The run polled while waiting_hitl but the steps only polled while
 * 'running', so after the decision the header said "complete" while the
 * timeline kept the gate "waiting_hitl", the publish step "running", and the
 * decided gate twice (suspended row + resumed row).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import WorkflowRunDetailPage from '../WorkflowRunDetailPage';
import { latestStepRows } from '../stepRows';

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

const run = (status: string) => ({
  run_id: 'run-1', workflow_id: 'wf-1', workflow_name: 'Weekly report', status,
  inputs: {}, outputs: {}, error: null, started_at: '2026-01-01T00:00:00Z',
  finished_at: status === 'complete' ? '2026-01-01T00:05:00Z' : null,
  duration_ms: null, step_count: 3, cost_usd: 0,
});

const step = (step_id: string, status: string, started_at: string) => ({
  step_id, step_type: step_id === 'manager_approval' ? 'hitl' : 'transform', status,
  output: null, error: null, started_at, finished_at: null, duration_ms: null,
});

const WAITING_STEPS = [
  step('draft_report', 'complete', '2026-01-01T00:00:01Z'),
  step('manager_approval', 'waiting_hitl', '2026-01-01T00:00:02Z'),
];
const FINAL_STEPS = [
  step('draft_report', 'complete', '2026-01-01T00:00:01Z'),
  step('manager_approval', 'waiting_hitl', '2026-01-01T00:00:02Z'),
  step('manager_approval', 'complete', '2026-01-01T00:04:00Z'),
  step('publish_summary', 'complete', '2026-01-01T00:04:01Z'),
];

describe('WorkflowRunDetailPage after an approval', () => {
  beforeEach(() => {
    vi.mocked(workflowEngineApi.getRun).mockResolvedValue(run('waiting_hitl') as any);
    vi.mocked(workflowEngineApi.getRunSteps).mockResolvedValue(WAITING_STEPS as any);
  });

  it('refetches the steps when the run status changes and shows each step once', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/workflow-runs/run-1']}>
          <Routes>
            <Route path="/workflow-runs/:runId" element={<WorkflowRunDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const list = await screen.findByRole('list', { name: /step results/i });
    await waitFor(() => expect(within(list).getByText('manager_approval')).toBeInTheDocument());

    // The reviewer approves; the worker finishes the run.
    vi.mocked(workflowEngineApi.getRun).mockResolvedValue(run('complete') as any);
    vi.mocked(workflowEngineApi.getRunSteps).mockResolvedValue(FINAL_STEPS as any);
    // Only the RUN query is refreshed (its 3 s poll) — the steps must follow.
    await act(async () => {
      await qc.invalidateQueries({ queryKey: ['workflow-engine', 'run', 'run-1'] });
    });

    await waitFor(() =>
      expect(within(screen.getByRole('list', { name: /step results/i })).getByText('publish_summary')).toBeInTheDocument(),
    );
    const timeline = screen.getByRole('list', { name: /step results/i });
    expect(within(timeline).getAllByText('manager_approval')).toHaveLength(1);
    expect(within(timeline).queryByText('waiting_hitl')).not.toBeInTheDocument();
  });

  it('latestStepRows keeps the latest row per step at its first position', () => {
    const rows = latestStepRows(FINAL_STEPS as any);
    expect(rows.map((r) => [r.step_id, r.status])).toEqual([
      ['draft_report', 'complete'],
      ['manager_approval', 'complete'],
      ['publish_summary', 'complete'],
    ]);
  });
});
