/**
 * Tests for ApprovalInboxPage — HITL inbox.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import ApprovalInboxPage from '../ApprovalInboxPage';

vi.mock('../../../lib/api/client', () => ({
  workflowEngineApi: {
    listApprovals: vi.fn(),
    approvalStats: vi.fn(),
    decideApproval: vi.fn(),
  },
}));

import { workflowEngineApi } from '../../../lib/api/client';

const mockApprovals = [
  {
    request_id: 'req-1',
    run_id: 'run-123',
    step_id: 'review',
    workflow_id: 'wf-1',
    priority: 'critical',
    status: 'pending',
    context: [{ display_type: 'json', title: 'Risk Score', data: { score: 0.9 } }],
    actions: [{ id: 'approve', label: 'Approve' }, { id: 'reject', label: 'Reject' }],
    deadline_at: null,
    created_at: new Date().toISOString(),
  },
  {
    request_id: 'req-2',
    run_id: 'run-456',
    step_id: 'compliance',
    workflow_id: 'wf-2',
    priority: 'medium',
    status: 'pending',
    context: [],
    actions: [],
    deadline_at: null,
    created_at: new Date().toISOString(),
  },
];

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ApprovalInboxPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('ApprovalInboxPage', () => {
  beforeEach(() => {
    vi.mocked(workflowEngineApi.listApprovals).mockResolvedValue({
      items: mockApprovals, total: 2,
    });
    vi.mocked(workflowEngineApi.approvalStats).mockResolvedValue({
      pending_count: 2, total_requests: 5, avg_resolution_seconds: 1800,
    });
  });

  it('renders page heading', async () => {
    renderPage();
    expect(screen.getByRole('heading', { name: /approval inbox/i })).toBeInTheDocument();
  });

  it('shows pending count badge', async () => {
    renderPage();
    await waitFor(() => screen.getByText('2'));
  });

  it('renders approval cards', async () => {
    renderPage();
    await waitFor(() => {
      const articles = screen.getAllByRole('article');
      expect(articles.length).toBe(2);
    });
  });

  it('shows priority badge for critical', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/critical/i)).toBeInTheDocument();
    });
  });

  it('shows approve and reject buttons', async () => {
    renderPage();
    await waitFor(() => {
      const approveButtons = screen.getAllByRole('button', { name: /approve/i });
      expect(approveButtons.length).toBeGreaterThan(0);
    });
  });

  it('shows empty state when no approvals', async () => {
    vi.mocked(workflowEngineApi.listApprovals).mockResolvedValue({ items: [], total: 0 });
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/all caught up/i)).toBeInTheDocument();
    });
  });

  it('shows avg resolution time', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/30min/i)).toBeInTheDocument();
    });
  });

  it('shows context items', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Risk Score')).toBeInTheDocument();
    });
  });
});
