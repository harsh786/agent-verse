/**
 * Tests for WorkflowListPage component.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import WorkflowListPage from '../WorkflowListPage';

// Mock the API
vi.mock('../../../lib/api/client', () => ({
  workflowEngineApi: {
    list: vi.fn(),
    delete: vi.fn(),
    create: vi.fn(),
  },
}));

import { workflowEngineApi } from '../../../lib/api/client';

const mockWorkflows = [
  {
    id: 'wf-1', name: 'KYC Workflow', description: 'Customer verification',
    status: 'published', version: '1', labels: {}, trigger_type: 'webhook',
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 'wf-2', name: 'Invoice Processing', description: 'AP automation',
    status: 'draft', version: '2', labels: {}, trigger_type: 'schedule',
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
    vi.mocked(workflowEngineApi.list).mockResolvedValue({
      items: mockWorkflows as any,
      total: 2,
      page: 1,
      per_page: 100,
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
});
