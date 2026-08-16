import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import WorkflowAnalyticsPage from '../WorkflowAnalyticsPage';

vi.mock('../../../lib/api/client', () => ({
  workflowEngineApi: {
    get: vi.fn().mockResolvedValue({
      id: 'wf-1', name: 'Test WF', status: 'published',
      version: '1', labels: {}, created_at: '', updated_at: '',
    }),
    workflowAnalytics: vi.fn().mockResolvedValue({
      total_runs: 42, success_rate: 0.95, total_cost_usd: 1.23, avg_duration_seconds: 30,
    }),
  },
}));

function wrapAnalytics() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/workflows/wf-1/analytics']}>
        <Routes>
          <Route path="/workflows/:id/analytics" element={<WorkflowAnalyticsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('WorkflowAnalyticsPage', () => {
  it('renders analytics heading', async () => {
    wrapAnalytics();
    expect(screen.getByRole('heading', { name: /analytics/i })).toBeInTheDocument();
  });

  it('shows stat cards after data loads', async () => {
    wrapAnalytics();
    await new Promise((r) => setTimeout(r, 200));
    expect(screen.getByText('Total Runs')).toBeInTheDocument();
    expect(screen.getByText('Success Rate')).toBeInTheDocument();
  });

  it('shows chart sections', async () => {
    wrapAnalytics();
    await new Promise((r) => setTimeout(r, 200));
    expect(screen.getByText(/runs per day/i)).toBeInTheDocument();
  });

  it('shows back link', () => {
    wrapAnalytics();
    expect(screen.getByRole('link', { name: /back to builder/i })).toBeInTheDocument();
  });
});

describe('WorkflowSettingsPage smoke', () => {
  it('module exports a component', async () => {
    const { default: WorkflowSettingsPage } = await import('../WorkflowSettingsPage');
    expect(typeof WorkflowSettingsPage).toBe('function');
  });
});
