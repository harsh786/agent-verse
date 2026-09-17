/**
 * Tests for ApprovalInboxPage — HITL inbox.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
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

  it('filters by priority when a filter button is clicked', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByRole('article').length).toBe(2);
    });

    const criticalButton = screen.getByRole('button', { name: 'critical' });
    fireEvent.click(criticalButton);

    expect(criticalButton).toHaveAttribute('aria-pressed', 'true');
    await waitFor(() => {
      expect(vi.mocked(workflowEngineApi.listApprovals)).toHaveBeenCalledWith(
        expect.objectContaining({ priority: 'critical' })
      );
    });

    // Switching back to "All" sends an undefined priority.
    const allButton = screen.getByRole('button', { name: 'All' });
    fireEvent.click(allButton);
    expect(allButton).toHaveAttribute('aria-pressed', 'true');
    await waitFor(() => {
      expect(vi.mocked(workflowEngineApi.listApprovals)).toHaveBeenCalledWith(
        expect.objectContaining({ priority: undefined })
      );
    });
  });

  it('refetches the inbox when the refresh button is clicked', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByRole('article').length).toBe(2);
    });

    const callsBefore = vi.mocked(workflowEngineApi.listApprovals).mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: /refresh inbox/i }));

    await waitFor(() => {
      expect(vi.mocked(workflowEngineApi.listApprovals).mock.calls.length).toBeGreaterThan(callsBefore);
    });
  });

  it('selects a request and reveals bulk actions', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByRole('article').length).toBe(2);
    });

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select request req-1' }));

    expect(screen.getByText('1 selected')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /bulk approve selected/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /bulk reject selected/i })).toBeInTheDocument();

    // Deselecting hides the bulk-action bar again.
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select request req-1' }));
    expect(screen.queryByText('1 selected')).not.toBeInTheDocument();
  });

  it('bulk-approves selected requests and clears the selection', async () => {
    vi.mocked(workflowEngineApi.decideApproval).mockResolvedValue({ status: 'approved' });
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByRole('article').length).toBe(2);
    });

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select request req-1' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select request req-2' }));
    expect(screen.getByText('2 selected')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /bulk approve selected/i }));

    await waitFor(() => {
      expect(workflowEngineApi.decideApproval).toHaveBeenCalledWith('req-1', { action: 'approved', note: undefined });
      expect(workflowEngineApi.decideApproval).toHaveBeenCalledWith('req-2', { action: 'approved', note: undefined });
    });
    expect(screen.queryByText('2 selected')).not.toBeInTheDocument();
  });

  it('bulk-rejects selected requests', async () => {
    vi.mocked(workflowEngineApi.decideApproval).mockResolvedValue({ status: 'rejected' });
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByRole('article').length).toBe(2);
    });

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select request req-2' }));
    fireEvent.click(screen.getByRole('button', { name: /bulk reject selected/i }));

    await waitFor(() => {
      expect(workflowEngineApi.decideApproval).toHaveBeenCalledWith('req-2', { action: 'rejected', note: undefined });
    });
  });

  it('decides a request via the custom DSL actions', async () => {
    vi.mocked(workflowEngineApi.decideApproval).mockResolvedValue({ status: 'approved' });
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByRole('article').length).toBe(2);
    });

    // req-1 has custom `actions` [{approve},{reject}] — clicking one of these
    // exercises the custom-action render branch (as opposed to the default
    // Approve/Reject buttons rendered for req-2, which has no `actions`).
    const req1Card = screen.getByRole('article', { name: /run run-123/i });
    fireEvent.click(within(req1Card).getByRole('button', { name: /approve request/i }));

    await waitFor(() => {
      expect(workflowEngineApi.decideApproval).toHaveBeenCalledWith('req-1', {
        action: 'approve',
        note: undefined,
      });
    });
  });

  it('decides a request via the default reject button with a custom third-party action label', async () => {
    vi.mocked(workflowEngineApi.listApprovals).mockResolvedValue({
      items: [
        {
          ...mockApprovals[0],
          actions: [{ id: 'escalate', label: 'Escalate' }],
        },
      ],
      total: 1,
    });
    vi.mocked(workflowEngineApi.decideApproval).mockResolvedValue({ status: 'escalated' });
    renderPage();

    const escalateButton = await screen.findByRole('button', { name: /escalate request/i });
    fireEvent.click(escalateButton);

    await waitFor(() => {
      expect(workflowEngineApi.decideApproval).toHaveBeenCalledWith('req-1', {
        action: 'escalate',
        note: undefined,
      });
    });
  });

  it('approves and rejects requests without custom actions using the default buttons', async () => {
    vi.mocked(workflowEngineApi.decideApproval).mockResolvedValue({ status: 'approved' });
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByRole('article').length).toBe(2);
    });

    // req-2 has no `actions`, so it renders the default Approve/Reject pair.
    const card = screen.getByRole('article', { name: /run run-456/i });
    fireEvent.click(within(card).getByRole('button', { name: /^approve request$/i }));

    await waitFor(() => {
      expect(workflowEngineApi.decideApproval).toHaveBeenCalledWith('req-2', {
        action: 'approved',
        note: undefined,
      });
    });

    fireEvent.click(within(card).getByRole('button', { name: /^reject request$/i }));
    await waitFor(() => {
      expect(workflowEngineApi.decideApproval).toHaveBeenCalledWith('req-2', {
        action: 'rejected',
        note: undefined,
      });
    });
  });

  it('toggles the note field and includes the note when deciding', async () => {
    vi.mocked(workflowEngineApi.listApprovals).mockResolvedValue({
      items: [mockApprovals[1]],
      total: 1,
    });
    vi.mocked(workflowEngineApi.decideApproval).mockResolvedValue({ status: 'approved' });
    renderPage();

    const card = await screen.findByRole('article');
    const noteToggle = within(card).getByRole('button', { name: 'Add note' });
    expect(noteToggle).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(noteToggle);
    expect(within(card).getByRole('button', { name: 'Hide note field' })).toHaveAttribute('aria-expanded', 'true');

    const textarea = within(card).getByLabelText('Decision note');
    fireEvent.change(textarea, { target: { value: 'Looks good to me' } });

    fireEvent.click(within(card).getByRole('button', { name: /^approve request$/i }));

    await waitFor(() => {
      expect(workflowEngineApi.decideApproval).toHaveBeenCalledWith('req-2', {
        action: 'approved',
        note: 'Looks good to me',
      });
    });

    // Toggling again hides the note field.
    fireEvent.click(within(card).getByRole('button', { name: 'Hide note field' }));
    expect(within(card).queryByLabelText('Decision note')).not.toBeInTheDocument();
  });

  it('shows a spinner on the deciding card while the mutation is in flight', async () => {
    let resolveDecide!: (value: unknown) => void;
    vi.mocked(workflowEngineApi.decideApproval).mockReturnValue(
      new Promise((resolve) => {
        resolveDecide = resolve;
      })
    );
    vi.mocked(workflowEngineApi.listApprovals).mockResolvedValue({
      items: [mockApprovals[1]],
      total: 1,
    });
    renderPage();

    const card = await screen.findByRole('article');
    const approveButton = within(card).getByRole('button', { name: /^approve request$/i });
    fireEvent.click(approveButton);

    await waitFor(() => {
      expect(approveButton).toBeDisabled();
    });

    resolveDecide({ status: 'approved' });
    await waitFor(() => {
      expect(approveButton).not.toBeDisabled();
    });
  });

  it('renders an SLA countdown for an urgent deadline and an overdue deadline', async () => {
    const soon = new Date(Date.now() + 60 * 60 * 1000).toISOString(); // 1h away -> urgent
    const past = new Date(Date.now() - 60 * 60 * 1000).toISOString(); // overdue
    vi.mocked(workflowEngineApi.listApprovals).mockResolvedValue({
      items: [
        { ...mockApprovals[0], request_id: 'req-urgent', deadline_at: soon },
        { ...mockApprovals[1], request_id: 'req-overdue', deadline_at: past },
      ],
      total: 2,
    });
    renderPage();

    await waitFor(() => {
      expect(screen.getByLabelText(/SLA deadline: \d+h \d+m remaining/)).toBeInTheDocument();
      expect(screen.getByLabelText('SLA deadline: overdue')).toBeInTheDocument();
    });
    expect(screen.getByText('Overdue')).toBeInTheDocument();
  });

  it('falls back to the medium priority style for an unknown priority value', async () => {
    vi.mocked(workflowEngineApi.listApprovals).mockResolvedValue({
      items: [{ ...mockApprovals[0], priority: 'mystery' }],
      total: 1,
    });
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('mystery')).toBeInTheDocument();
    });
  });

  it('shows the pending count only when greater than zero and hides the badge otherwise', async () => {
    vi.mocked(workflowEngineApi.approvalStats).mockResolvedValue({
      pending_count: 0, total_requests: 0, avg_resolution_seconds: null,
    });
    renderPage();

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /approval inbox/i })).toBeInTheDocument();
    });
    expect(screen.queryByLabelText(/pending$/)).not.toBeInTheDocument();
    expect(screen.getByText((_, element) => element?.textContent === 'Avg resolution: —')).toBeInTheDocument();
  });
});
