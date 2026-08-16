/**
 * Tests for run-viewer components: RunTimeline, StepOutputInspector, RunCostSummary.
 */
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { RunTimeline } from '../run-viewer/RunTimeline';
import { StepOutputInspector } from '../run-viewer/StepOutputInspector';
import { RunCostSummary } from '../run-viewer/RunCostSummary';
import type { WEStepResult, WERun } from '../../../lib/api/client';

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>
  );
}

const mockSteps: WEStepResult[] = [
  {
    step_id: 'fetch_data', step_type: 'tool', status: 'complete',
    output: { result: 'fetched', count: 42 },
    error: undefined, started_at: '2026-01-01T00:00:00Z',
    finished_at: '2026-01-01T00:00:10Z', duration_ms: 10000,
  },
  {
    step_id: 'analyze', step_type: 'llm', status: 'complete',
    output: { summary: 'Risk is low', confidence: 0.95 },
    error: undefined, started_at: '2026-01-01T00:00:10Z',
    finished_at: '2026-01-01T00:00:20Z', duration_ms: 10000,
  },
  {
    step_id: 'review', step_type: 'hitl', status: 'waiting_hitl',
    output: undefined, error: undefined,
    started_at: '2026-01-01T00:00:20Z', finished_at: undefined, duration_ms: undefined,
  },
  {
    step_id: 'failed_step', step_type: 'http', status: 'failed',
    output: undefined, error: 'Connection timeout after 30s',
    started_at: '2026-01-01T00:01:00Z', finished_at: '2026-01-01T00:01:30Z', duration_ms: 30000,
  },
];

const mockRun: WERun = {
  run_id: 'run-1', workflow_id: 'wf-1', status: 'failed',
  inputs: { email: 'test@example.com' }, outputs: {},
  error: 'Step failed_step failed', cost_usd: 0.0456,
  step_count: 4, started_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:01:30Z',
  duration_ms: 90000,
};

// ── RunTimeline ───────────────────────────────────────────────────────────────

describe('RunTimeline', () => {
  it('renders all steps in order', () => {
    wrap(<RunTimeline steps={mockSteps} />);
    expect(screen.getByText('fetch_data')).toBeInTheDocument();
    expect(screen.getByText('analyze')).toBeInTheDocument();
    expect(screen.getByText('review')).toBeInTheDocument();
    expect(screen.getByText('failed_step')).toBeInTheDocument();
  });

  it('shows step types', () => {
    wrap(<RunTimeline steps={mockSteps} />);
    expect(screen.getByText('tool')).toBeInTheDocument();
    expect(screen.getByText('llm')).toBeInTheDocument();
  });

  it('shows error message for failed steps', () => {
    wrap(<RunTimeline steps={mockSteps} />);
    expect(screen.getByText(/Connection timeout/)).toBeInTheDocument();
  });

  it('shows duration for timed steps', () => {
    wrap(<RunTimeline steps={mockSteps} />);
    // 10000ms = 10s should appear somewhere
    const durationTexts = screen.getAllByText((content) => content.includes('10'));
    expect(durationTexts.length).toBeGreaterThan(0);
  });

  it('renders accessible list', () => {
    wrap(<RunTimeline steps={mockSteps} />);
    const list = screen.getByRole('list', { name: /timeline/i });
    expect(list).toBeInTheDocument();
  });

  it('clicking step shows output', () => {
    wrap(<RunTimeline steps={mockSteps} />);
    const fetchBtn = screen.getByRole('button', { name: /fetch_data/i });
    fireEvent.click(fetchBtn);
    expect(screen.getByText(/"result"/)).toBeInTheDocument();
  });

  it('clicking step again collapses it', () => {
    wrap(<RunTimeline steps={mockSteps} />);
    const fetchBtn = screen.getByRole('button', { name: /fetch_data/i });
    fireEvent.click(fetchBtn);
    fireEvent.click(fetchBtn);
    // Output should be gone
    expect(screen.queryByText(/"count"/)).toBeNull();
  });

  it('shows empty state when no steps', () => {
    wrap(<RunTimeline steps={[]} />);
    expect(screen.getByRole('status')).toBeInTheDocument();
  });
});

// ── StepOutputInspector ───────────────────────────────────────────────────────

describe('StepOutputInspector', () => {
  it('renders JSON output', () => {
    wrap(<StepOutputInspector data={{ result: 'ok', score: 42 }} />);
    expect(screen.getByText(/"result"/)).toBeInTheDocument();
    expect(screen.getByText(/"score"/)).toBeInTheDocument();
  });

  it('renders null gracefully', () => {
    wrap(<StepOutputInspector data={null} />);
    expect(screen.getByText(/no output data/i)).toBeInTheDocument();
  });

  it('renders arrays', () => {
    wrap(<StepOutputInspector data={[1, 2, 3]} title="Items" />);
    expect(screen.getByRole('region', { name: /items/i })).toBeInTheDocument();
  });

  it('renders nested objects', () => {
    wrap(<StepOutputInspector data={{ nested: { deep: { value: 'found' } } }} />);
    expect(screen.getByText(/"nested"/)).toBeInTheDocument();
  });

  it('shows copy button', () => {
    wrap(<StepOutputInspector data={{ x: 1 }} />);
    expect(screen.getByRole('button', { name: /copy/i })).toBeInTheDocument();
  });

  it('uses custom title', () => {
    wrap(<StepOutputInspector data={{ x: 1 }} title="Step Input" />);
    expect(screen.getByText('Step Input')).toBeInTheDocument();
  });

  it('has accessible region', () => {
    wrap(<StepOutputInspector data={{ x: 1 }} title="Output" />);
    expect(screen.getByRole('region', { name: /output data/i })).toBeInTheDocument();
  });
});

// ── RunCostSummary ────────────────────────────────────────────────────────────

describe('RunCostSummary', () => {
  it('renders all stat cards', () => {
    wrap(<RunCostSummary run={mockRun} />);
    expect(screen.getByText('Total Cost')).toBeInTheDocument();
    expect(screen.getByText('Duration')).toBeInTheDocument();
    expect(screen.getByText('Steps')).toBeInTheDocument();
    expect(screen.getByText('Status')).toBeInTheDocument();
  });

  it('shows formatted cost', () => {
    wrap(<RunCostSummary run={mockRun} />);
    expect(screen.getByText('$0.0456')).toBeInTheDocument();
  });

  it('shows formatted duration', () => {
    wrap(<RunCostSummary run={mockRun} />);
    // 90000ms = 1m 30s
    expect(screen.getByText(/1m 30s|90s/)).toBeInTheDocument();
  });

  it('shows step count', () => {
    wrap(<RunCostSummary run={mockRun} />);
    expect(screen.getByText('4')).toBeInTheDocument();
  });

  it('shows run status', () => {
    wrap(<RunCostSummary run={mockRun} />);
    expect(screen.getByText('failed')).toBeInTheDocument();
  });

  it('renders as accessible list', () => {
    wrap(<RunCostSummary run={mockRun} />);
    expect(screen.getByRole('list', { name: /run cost summary/i })).toBeInTheDocument();
  });

  it('overrides step count when provided', () => {
    wrap(<RunCostSummary run={mockRun} stepCount={7} />);
    expect(screen.getByText('7')).toBeInTheDocument();
  });
});
