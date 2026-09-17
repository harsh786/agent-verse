/**
 * Tests for WorkflowListPage component.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import WorkflowListPage from '../WorkflowListPage';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

// Mock the API
vi.mock('../../../lib/api/client', () => ({
  workflowEngineApi: {
    list: vi.fn(),
    delete: vi.fn(),
    create: vi.fn(),
    listRuns: vi.fn(),
    pauseRun: vi.fn(),
    resumeRun: vi.fn(),
    cancelRun: vi.fn(),
    trigger: vi.fn(),
  },
}));

import { workflowEngineApi } from '../../../lib/api/client';

// Keep the YAML-create modal a simple stub so we can exercise the callbacks
// WorkflowListPage wires to it without depending on its internal form.
vi.mock('../builder/WorkflowYamlCreateModal', () => ({
  WorkflowYamlCreateModal: ({ onClose, onCreated }: { onClose: () => void; onCreated: (id: string) => void }) => (
    <div data-testid="yaml-modal">
      <button onClick={onClose}>close-yaml-modal</button>
      <button onClick={() => onCreated('wf-new')}>confirm-yaml-create</button>
    </div>
  ),
}));

const mockWorkflows = [
  {
    id: 'wf-1', name: 'KYC Workflow', description: 'Customer verification',
    status: 'published', version: '1', labels: {}, trigger_type: 'webhook',
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 'wf-2', name: 'Invoice Processing', description: 'AP automation',
    status: 'draft', version: '2', labels: {}, trigger_type: 'schedule',
    schedule_cron: '0 9 * * *',
    created_at: '2026-01-02T00:00:00Z', updated_at: '2026-01-02T00:00:00Z',
  },
];

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <WorkflowListPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('WorkflowListPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(workflowEngineApi.list).mockResolvedValue({
      items: mockWorkflows as any,
      total: 2,
      page: 1,
      per_page: 100,
    });
    vi.mocked(workflowEngineApi.listRuns).mockResolvedValue({
      items: [] as any, total: 0, page: 1, per_page: 100,
    });
  });

  it('renders page heading', async () => {
    renderPage();
    expect(screen.getByRole('heading', { name: /workflows/i })).toBeInTheDocument();
  });

  it('renders workflow cards after loading', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('KYC Workflow')).toBeInTheDocument();
    });
    expect(screen.getByText('Invoice Processing')).toBeInTheDocument();
  });

  it('shows empty state when no workflows', async () => {
    vi.mocked(workflowEngineApi.list).mockResolvedValue({
      items: [] as any, total: 0, page: 1, per_page: 100,
    });
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/no workflows yet/i)).toBeInTheDocument();
    });
  });

  it('filters by search term', async () => {
    renderPage();
    await waitFor(() => screen.getByText('KYC Workflow'));

    const searchInput = screen.getByRole('searchbox', { name: /search/i });
    fireEvent.change(searchInput, { target: { value: 'kyc' } });

    expect(screen.getByText('KYC Workflow')).toBeInTheDocument();
    expect(screen.queryByText('Invoice Processing')).toBeNull();
  });

  it('shows status badges', async () => {
    renderPage();
    await waitFor(() => screen.getByText('KYC Workflow'));

    // Both status badge text values should appear somewhere
    const statusTexts = screen.getAllByText(/published|draft/i);
    expect(statusTexts.length).toBeGreaterThanOrEqual(2);
  });

  it('shows New Workflow button', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole('button', { name: /new workflow/i })).toBeInTheDocument());
  });

  it('has accessible list/listitem roles for workflow cards', async () => {
    renderPage();
    await waitFor(() => screen.getByText('KYC Workflow'));
    const list = screen.getByRole('list', { name: /workflow list/i });
    const items = within(list).getAllByRole('listitem');
    expect(items.length).toBe(2);
  });

  it('shows loading skeletons initially', () => {
    vi.mocked(workflowEngineApi.list).mockImplementation(
      () => new Promise(() => {}) // never resolves
    );
    renderPage();
    // Loading state: no articles visible yet
    expect(screen.queryAllByRole('article').length).toBe(0);
  });

  it('shows an error state with a retry button that refetches', async () => {
    vi.mocked(workflowEngineApi.list)
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValue({ items: mockWorkflows as any, total: 2, page: 1, per_page: 100 });
    renderPage();

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/failed to load workflows/i);
    });

    fireEvent.click(screen.getByRole('button', { name: /retry/i }));

    await waitFor(() => {
      expect(screen.getByText('KYC Workflow')).toBeInTheDocument();
    });
    expect(vi.mocked(workflowEngineApi.list)).toHaveBeenCalledTimes(2);
  });

  it('refetches the list when the refresh button is clicked', async () => {
    renderPage();
    await waitFor(() => screen.getByText('KYC Workflow'));

    const before = vi.mocked(workflowEngineApi.list).mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: /refresh list/i }));

    await waitFor(() => {
      expect(vi.mocked(workflowEngineApi.list).mock.calls.length).toBeGreaterThan(before);
    });
  });

  it('filters by description as well as name', async () => {
    renderPage();
    await waitFor(() => screen.getByText('KYC Workflow'));

    const searchInput = screen.getByRole('searchbox', { name: /search/i });
    fireEvent.change(searchInput, { target: { value: 'automation' } });

    expect(screen.getByText('Invoice Processing')).toBeInTheDocument();
    expect(screen.queryByText('KYC Workflow')).toBeNull();
  });

  it('shows the "(search matches loaded page)" hint and count when searching', async () => {
    renderPage();
    await waitFor(() => screen.getByText('KYC Workflow'));

    fireEvent.change(screen.getByRole('searchbox', { name: /search/i }), {
      target: { value: 'kyc' },
    });

    expect(screen.getByText(/search matches loaded page/i)).toBeInTheDocument();
    expect(screen.getByText(/1 of 2 workflow/i)).toBeInTheDocument();
  });

  describe('status filter tabs', () => {
    it('defaults to the Published tab and requests that status', async () => {
      renderPage();
      await waitFor(() => screen.getByText('KYC Workflow'));

      const publishedTab = screen.getByRole('button', { name: 'published' });
      expect(publishedTab).toHaveAttribute('aria-pressed', 'true');
      expect(workflowEngineApi.list).toHaveBeenCalledWith(
        expect.objectContaining({ status: 'published' })
      );
    });

    it('switches to the All tab and requests an undefined status filter', async () => {
      renderPage();
      await waitFor(() => screen.getByText('KYC Workflow'));

      fireEvent.click(screen.getByRole('button', { name: 'All' }));

      await waitFor(() => {
        expect(screen.getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'true');
      });
      expect(workflowEngineApi.list).toHaveBeenCalledWith(
        expect.objectContaining({ status: undefined })
      );
    });

    it('excludes archived workflows from the All tab', async () => {
      vi.mocked(workflowEngineApi.list).mockResolvedValue({
        items: [
          ...mockWorkflows,
          {
            id: 'wf-3', name: 'Archived One', description: '', status: 'archived',
            version: '1', labels: {}, trigger_type: 'manual',
            created_at: '2026-01-03T00:00:00Z', updated_at: '2026-01-03T00:00:00Z',
          },
        ] as any,
        total: 3, page: 1, per_page: 100,
      });
      renderPage();
      await waitFor(() => screen.getByText('KYC Workflow'));

      fireEvent.click(screen.getByRole('button', { name: 'All' }));

      await waitFor(() => {
        expect(screen.getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'true');
      });
      expect(screen.queryByText('Archived One')).toBeNull();
    });

    it('switches to the Archived tab and requests that status', async () => {
      renderPage();
      await waitFor(() => screen.getByText('KYC Workflow'));

      fireEvent.click(screen.getByRole('button', { name: 'archived' }));

      await waitFor(() => {
        expect(screen.getByRole('button', { name: 'archived' })).toHaveAttribute('aria-pressed', 'true');
      });
      expect(workflowEngineApi.list).toHaveBeenCalledWith(
        expect.objectContaining({ status: 'archived' })
      );
    });
  });

  describe('load more / pagination', () => {
    it('shows a Load more button when more workflows exist than are loaded, and grows the window', async () => {
      vi.mocked(workflowEngineApi.list).mockResolvedValue({
        items: mockWorkflows as any, total: 10, page: 1, per_page: 60,
      });
      renderPage();
      await waitFor(() => screen.getByText('KYC Workflow'));

      const loadMore = screen.getByRole('button', { name: /load more workflows/i });
      expect(loadMore).toBeInTheDocument();

      fireEvent.click(loadMore);

      await waitFor(() => {
        expect(workflowEngineApi.list).toHaveBeenCalledWith(
          expect.objectContaining({ per_page: 120 })
        );
      });
    });

    it('hides the Load more button once every workflow has been loaded', async () => {
      renderPage();
      await waitFor(() => screen.getByText('KYC Workflow'));
      expect(screen.queryByRole('button', { name: /load more workflows/i })).toBeNull();
    });
  });

  describe('workflow creation', () => {
    it('creates a blank workflow and navigates to its editor on success', async () => {
      vi.mocked(workflowEngineApi.create).mockResolvedValue({ id: 'wf-new' } as any);
      renderPage();
      await waitFor(() => screen.getByRole('button', { name: /new workflow/i }));

      fireEvent.click(screen.getByRole('button', { name: /new workflow/i }));

      await waitFor(() => {
        expect(workflowEngineApi.create).toHaveBeenCalledWith(
          expect.objectContaining({ name: 'Untitled Workflow' })
        );
      });
      await waitFor(() => {
        expect(mockNavigate).toHaveBeenCalledWith('/workflows/wf-new/edit');
      });
    });

    it('swallows a create error without navigating (handled by toast)', async () => {
      vi.mocked(workflowEngineApi.create).mockRejectedValue(new Error('nope'));
      renderPage();
      await waitFor(() => screen.getByRole('button', { name: /new workflow/i }));

      fireEvent.click(screen.getByRole('button', { name: /new workflow/i }));

      await waitFor(() => expect(workflowEngineApi.create).toHaveBeenCalled());
      expect(mockNavigate).not.toHaveBeenCalledWith(expect.stringContaining('/edit'));
    });

    it('navigates to the marketplace when Templates is clicked', async () => {
      renderPage();
      await waitFor(() => screen.getByRole('button', { name: /browse workflow templates/i }));

      fireEvent.click(screen.getByRole('button', { name: /browse workflow templates/i }));
      expect(mockNavigate).toHaveBeenCalledWith('/workflows/marketplace');
    });

    it('opens the YAML create modal, and creating navigates + invalidates the list', async () => {
      renderPage();
      await waitFor(() => screen.getByText('KYC Workflow'));

      fireEvent.click(screen.getByRole('button', { name: /create workflow from yaml/i }));
      expect(screen.getByTestId('yaml-modal')).toBeInTheDocument();

      fireEvent.click(screen.getByText('confirm-yaml-create'));

      expect(screen.queryByTestId('yaml-modal')).toBeNull();
      expect(mockNavigate).toHaveBeenCalledWith('/workflows/wf-new/edit');
    });

    it('closes the YAML create modal without navigating', async () => {
      renderPage();
      await waitFor(() => screen.getByText('KYC Workflow'));

      fireEvent.click(screen.getByRole('button', { name: /create workflow from yaml/i }));
      fireEvent.click(screen.getByText('close-yaml-modal'));

      expect(screen.queryByTestId('yaml-modal')).toBeNull();
    });

    it('opens the YAML create modal from the empty state as well', async () => {
      vi.mocked(workflowEngineApi.list).mockResolvedValue({
        items: [] as any, total: 0, page: 1, per_page: 100,
      });
      renderPage();
      await waitFor(() => screen.getByText(/no workflows yet/i));

      fireEvent.click(screen.getByRole('button', { name: /create from yaml/i }));
      expect(screen.getByTestId('yaml-modal')).toBeInTheDocument();
    });

    it('creates a blank workflow from the empty state CTA', async () => {
      vi.mocked(workflowEngineApi.list).mockResolvedValue({
        items: [] as any, total: 0, page: 1, per_page: 100,
      });
      vi.mocked(workflowEngineApi.create).mockResolvedValue({ id: 'wf-empty' } as any);
      renderPage();
      await waitFor(() => screen.getByText(/no workflows yet/i));

      fireEvent.click(screen.getByRole('button', { name: /create blank workflow/i }));

      await waitFor(() => {
        expect(mockNavigate).toHaveBeenCalledWith('/workflows/wf-empty/edit');
      });
    });

    it('browses templates from the empty state CTA', async () => {
      vi.mocked(workflowEngineApi.list).mockResolvedValue({
        items: [] as any, total: 0, page: 1, per_page: 100,
      });
      renderPage();
      await waitFor(() => screen.getByText(/no workflows yet/i));

      fireEvent.click(screen.getByRole('button', { name: /browse templates/i }));
      expect(mockNavigate).toHaveBeenCalledWith('/workflows/marketplace');
    });
  });

  describe('workflow card actions', () => {
    it('deletes a workflow when its delete button is clicked', async () => {
      // Keep the mutation pending so the optimistic removal is observable
      // before onSettled's invalidateQueries triggers a refetch.
      let resolveDelete!: () => void;
      vi.mocked(workflowEngineApi.delete).mockImplementation(
        () => new Promise((resolve) => { resolveDelete = () => resolve(undefined as any); })
      );
      renderPage();
      await waitFor(() => screen.getByText('KYC Workflow'));

      fireEvent.click(screen.getByRole('button', { name: /delete workflow kyc workflow/i }));

      await waitFor(() => expect(workflowEngineApi.delete).toHaveBeenCalledWith('wf-1'));
      // Optimistic update removes the row immediately, ahead of the mutation settling.
      await waitFor(() => {
        expect(screen.queryByText('KYC Workflow')).toBeNull();
      });
      expect(screen.getByText('Invoice Processing')).toBeInTheDocument();

      resolveDelete();
    });

    it('rolls back the optimistic delete when the mutation fails', async () => {
      vi.mocked(workflowEngineApi.delete).mockRejectedValue(new Error('fail'));
      // Once rolled back, the follow-up invalidation refetch must resolve to the
      // full (un-deleted) list so the row can genuinely reappear.
      vi.mocked(workflowEngineApi.list).mockResolvedValue({
        items: mockWorkflows as any, total: 2, page: 1, per_page: 100,
      });
      renderPage();
      await waitFor(() => screen.getByText('KYC Workflow'));

      fireEvent.click(screen.getByRole('button', { name: /delete workflow kyc workflow/i }));

      await waitFor(() => expect(workflowEngineApi.delete).toHaveBeenCalled());
      // After rollback (and the settled refetch) the row reappears.
      await waitFor(() => {
        expect(screen.getByText('KYC Workflow')).toBeInTheDocument();
      });
    });

    it('only shows a Runs link for published workflows', async () => {
      renderPage();
      await waitFor(() => screen.getByText('KYC Workflow'));

      expect(screen.getByRole('link', { name: /view runs for kyc workflow/i })).toBeInTheDocument();
      expect(screen.queryByRole('link', { name: /view runs for invoice processing/i })).toBeNull();
    });

    it('links each card to its editor', async () => {
      renderPage();
      await waitFor(() => screen.getByText('KYC Workflow'));

      expect(screen.getByRole('link', { name: /open workflow kyc workflow/i }))
        .toHaveAttribute('href', '/workflows/wf-1/edit');
      expect(screen.getByRole('link', { name: /edit workflow kyc workflow/i }))
        .toHaveAttribute('href', '/workflows/wf-1/edit');
    });

    it('renders trigger badges for webhook and schedule types', async () => {
      renderPage();
      await waitFor(() => screen.getByText('KYC Workflow'));

      expect(screen.getByText('webhook')).toBeInTheDocument();
      // wf-2 has schedule_cron '0 9 * * *' -> daily 9am
      expect(screen.getByText(/daily 9am/i)).toBeInTheDocument();
    });

    it('renders a manual trigger badge when no trigger_type is set', async () => {
      vi.mocked(workflowEngineApi.list).mockResolvedValue({
        items: [{
          id: 'wf-4', name: 'Manual Flow', description: '', status: 'published',
          version: '1', labels: {}, trigger_type: 'manual',
          created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
        }] as any,
        total: 1, page: 1, per_page: 100,
      });
      renderPage();
      await waitFor(() => screen.getByText('Manual Flow'));
      expect(screen.getByText('manual')).toBeInTheDocument();
    });

    it('shows an unrecognized status with a generic alert icon badge', async () => {
      vi.mocked(workflowEngineApi.list).mockResolvedValue({
        items: [{
          id: 'wf-5', name: 'Odd Status', description: '', status: 'weird',
          version: '1', labels: {}, trigger_type: 'manual',
          created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
        }] as any,
        total: 1, page: 1, per_page: 100,
      });
      renderPage();
      await waitFor(() => screen.getByText('Odd Status'));
      expect(screen.getByText('weird')).toBeInTheDocument();
    });
  });

  describe('active run controls on cards', () => {
    function withActiveRun(status: string) {
      vi.mocked(workflowEngineApi.list).mockResolvedValue({
        items: [mockWorkflows[0]] as any, total: 1, page: 1, per_page: 100,
      });
      vi.mocked(workflowEngineApi.listRuns).mockResolvedValue({
        items: [{ run_id: 'run-1', workflow_id: 'wf-1', status }] as any,
        total: 1, page: 1, per_page: 100,
      });
    }

    it('shows Pause and Stop for a running run, and pausing invalidates active-runs', async () => {
      withActiveRun('running');
      vi.mocked(workflowEngineApi.pauseRun).mockResolvedValue({ run_id: 'run-1', status: 'paused' } as any);
      renderPage();
      await waitFor(() => screen.getByText('KYC Workflow'));

      expect(screen.getByText('running')).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: /pause run of run-1/i }));

      await waitFor(() => expect(workflowEngineApi.pauseRun).toHaveBeenCalledWith('run-1'));
    });

    it('shows Stop for a pending run', async () => {
      withActiveRun('pending');
      renderPage();
      await waitFor(() => screen.getByText('KYC Workflow'));

      expect(screen.getByRole('button', { name: /stop run of run-1/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /pause run of run-1/i })).toBeInTheDocument();
    });

    it('shows Resume and Stop for a paused run, and resuming calls the API', async () => {
      withActiveRun('paused');
      vi.mocked(workflowEngineApi.resumeRun).mockResolvedValue({ run_id: 'run-1', status: 'running' } as any);
      renderPage();
      await waitFor(() => screen.getByText('KYC Workflow'));

      fireEvent.click(screen.getByRole('button', { name: /resume run of run-1/i }));
      await waitFor(() => expect(workflowEngineApi.resumeRun).toHaveBeenCalledWith('run-1'));
      expect(screen.getByRole('button', { name: /stop run of run-1/i })).toBeInTheDocument();
    });

    it('stopping a run calls cancelRun', async () => {
      withActiveRun('running');
      vi.mocked(workflowEngineApi.cancelRun).mockResolvedValue({ run_id: 'run-1', status: 'cancelled' } as any);
      renderPage();
      await waitFor(() => screen.getByText('KYC Workflow'));

      fireEvent.click(screen.getByRole('button', { name: /stop run of run-1/i }));
      await waitFor(() => expect(workflowEngineApi.cancelRun).toHaveBeenCalledWith('run-1'));
    });

    it('shows a Re-run action (labeled "stopped") for a cancelled run and re-triggers on click', async () => {
      withActiveRun('cancelled');
      vi.mocked(workflowEngineApi.trigger).mockResolvedValue({ run_id: 'run-2' } as any);
      renderPage();
      await waitFor(() => screen.getByText('KYC Workflow'));

      expect(screen.getByText('stopped')).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: /re-run wf-1/i }));

      await waitFor(() => expect(workflowEngineApi.trigger).toHaveBeenCalledWith('wf-1', {}));
    });

    it('shows a Re-run action for a failed run', async () => {
      withActiveRun('failed');
      renderPage();
      await waitFor(() => screen.getByText('KYC Workflow'));
      expect(screen.getByRole('button', { name: /re-run wf-1/i })).toBeInTheDocument();
      expect(screen.getByText('failed')).toBeInTheDocument();
    });

    it('hides run controls entirely once the run is complete', async () => {
      withActiveRun('complete');
      renderPage();
      await waitFor(() => screen.getByText('KYC Workflow'));
      expect(screen.queryByRole('button', { name: /re-run wf-1/i })).toBeNull();
      expect(screen.queryByRole('button', { name: /pause run of run-1/i })).toBeNull();
    });

    it('shows a spinner while a run-control mutation is in flight', async () => {
      withActiveRun('running');
      let resolvePause!: () => void;
      vi.mocked(workflowEngineApi.pauseRun).mockImplementation(
        () => new Promise((resolve) => { resolvePause = () => resolve({ run_id: 'run-1', status: 'paused' } as any); })
      );
      const { container } = renderPage();
      await waitFor(() => screen.getByText('KYC Workflow'));

      fireEvent.click(screen.getByRole('button', { name: /pause run of run-1/i }));

      await waitFor(() => {
        expect(container.querySelector('.animate-spin')).not.toBeNull();
      });
      resolvePause();
    });
  });

  describe('shortCron / TriggerBadge formatting', () => {
    function withSchedule(cron?: string) {
      vi.mocked(workflowEngineApi.list).mockResolvedValue({
        items: [{
          id: 'wf-cron', name: 'Cron Flow', description: '', status: 'published',
          version: '1', labels: {}, trigger_type: 'schedule', schedule_cron: cron,
          created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
        }] as any,
        total: 1, page: 1, per_page: 100,
      });
    }

    it('shows a generic "schedule" label and "scheduled" title when no cron is given', async () => {
      withSchedule(undefined);
      renderPage();
      await waitFor(() => screen.getByText('Cron Flow'));
      const badge = screen.getByText('schedule');
      expect(badge.closest('span')).toHaveAttribute('title', 'scheduled');
    });

    it('falls back to the raw cron string when it has fewer than 5 fields', async () => {
      withSchedule('* * *');
      renderPage();
      await waitFor(() => screen.getByText('Cron Flow'));
      expect(screen.getByText('* * *')).toBeInTheDocument();
    });

    it('formats a "every Nm" interval cron', async () => {
      withSchedule('*/15 * * * *');
      renderPage();
      await waitFor(() => screen.getByText('Cron Flow'));
      expect(screen.getByText('every 15m')).toBeInTheDocument();
    });

    it('formats an hourly cron', async () => {
      withSchedule('0 * * * *');
      renderPage();
      await waitFor(() => screen.getByText('Cron Flow'));
      expect(screen.getByText('hourly')).toBeInTheDocument();
    });

    it('formats a weekly cron with a PM hour and a non-zero minute', async () => {
      withSchedule('30 14 * * 1');
      renderPage();
      await waitFor(() => screen.getByText('Cron Flow'));
      expect(screen.getByText('weekly 2:30pm')).toBeInTheDocument();
    });

    it('formats a monthly cron (specific day-of-month, every day-of-week)', async () => {
      withSchedule('0 10 15 * *');
      renderPage();
      await waitFor(() => screen.getByText('Cron Flow'));
      expect(screen.getByText('monthly 10am')).toBeInTheDocument();
    });

    it('formats noon (hour 12) as "12pm" rather than "0pm"', async () => {
      withSchedule('0 12 15 * *');
      renderPage();
      await waitFor(() => screen.getByText('Cron Flow'));
      expect(screen.getByText('monthly 12pm')).toBeInTheDocument();
    });

    it('falls back to "hour:minute" when the cron hour is not numeric', async () => {
      withSchedule('0 xx * * *');
      renderPage();
      await waitFor(() => screen.getByText('Cron Flow'));
      expect(screen.getByText('daily xx:0')).toBeInTheDocument();
    });

    it('shows the cron string as a tooltip when present', async () => {
      withSchedule('0 9 * * *');
      renderPage();
      await waitFor(() => screen.getByText('Cron Flow'));
      expect(screen.getByText('daily 9am').closest('span')).toHaveAttribute('title', 'cron: 0 9 * * *');
    });
  });

  describe('miscellaneous derived state', () => {
    it('falls back to items.length when the server omits a total count', async () => {
      vi.mocked(workflowEngineApi.list).mockResolvedValue({
        items: mockWorkflows as any, page: 1, per_page: 100,
      } as any);
      renderPage();
      await waitFor(() => screen.getByText('KYC Workflow'));
      expect(screen.getByText(/2 of 2 workflow/i)).toBeInTheDocument();
    });

    it('shows a spinner in the Load more button while a background refetch is in flight', async () => {
      vi.mocked(workflowEngineApi.list).mockResolvedValue({
        items: mockWorkflows as any, total: 10, page: 1, per_page: 60,
      });
      const { container } = renderPage();
      await waitFor(() => screen.getByText('KYC Workflow'));
      expect(screen.getByRole('button', { name: /load more workflows/i })).toBeInTheDocument();

      // Data already exists for this query key, so a manual refetch (via the
      // Refresh button) is a background update: isFetching flips true while
      // isLoading (and thus the existing content, including Load more) stays put.
      let resolveNext!: (v: any) => void;
      vi.mocked(workflowEngineApi.list).mockImplementation(
        () => new Promise((resolve) => { resolveNext = resolve; })
      );
      fireEvent.click(screen.getByRole('button', { name: /refresh list/i }));

      await waitFor(() => {
        expect(container.querySelector('.animate-spin')).not.toBeNull();
      });
      resolveNext({ items: mockWorkflows as any, total: 10, page: 1, per_page: 60 });
    });
  });
});
