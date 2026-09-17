/**
 * Tests for WorkflowMarketplacePage — template gallery.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import WorkflowMarketplacePage from '../WorkflowMarketplacePage';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

vi.mock('../../../lib/api/client', () => ({
  workflowEngineApi: {
    listTemplates: vi.fn(),
    listTemplateCategories: vi.fn(),
    forkTemplate: vi.fn(),
  },
}));

import { workflowEngineApi } from '../../../lib/api/client';

const mockTemplates = [
  {
    slug: 'kyc-automation',
    name: 'KYC Automation',
    description: 'Customer verification workflow',
    category: 'Financial Services',
    tags: ['kyc', 'compliance'],
    complexity: 'medium',
    popularity_score: 42,
  },
  {
    slug: 'sre-incident-response',
    name: 'SRE Incident Response',
    description: 'Automated incident triage',
    category: 'Engineering',
    tags: ['sre', 'devops'],
    complexity: 'complex',
    popularity_score: 18,
  },
];

const mockCategories = [
  { category: 'Financial Services', count: 5 },
  { category: 'Engineering', count: 3 },
];

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <WorkflowMarketplacePage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('WorkflowMarketplacePage', () => {
  beforeEach(() => {
    vi.mocked(workflowEngineApi.listTemplates).mockResolvedValue({
      items: mockTemplates, total: 2,
    });
    vi.mocked(workflowEngineApi.listTemplateCategories).mockResolvedValue(mockCategories);
    vi.mocked(workflowEngineApi.forkTemplate).mockResolvedValue({
      id: 'wf-new', name: 'KYC Automation (copy)', status: 'draft' as const,
      version: '1', labels: {}, description: '', created_at: '', updated_at: '',
    });
    mockNavigate.mockClear();
  });

  it('renders page heading', async () => {
    renderPage();
    expect(screen.getByRole('heading', { name: /template marketplace/i })).toBeInTheDocument();
  });

  it('renders template cards', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('KYC Automation')).toBeInTheDocument();
    });
    expect(screen.getByText('SRE Incident Response')).toBeInTheDocument();
  });

  it('shows category filter buttons', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /financial services/i })).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /engineering/i })).toBeInTheDocument();
  });

  it('shows Use Template buttons', async () => {
    renderPage();
    await waitFor(() => {
      const forkButtons = screen.getAllByText(/use template/i);
      expect(forkButtons.length).toBeGreaterThanOrEqual(2);
    });
  });

  it('shows total template count', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/2.*templates/i)).toBeInTheDocument();
    });
  });

  it('shows complexity badges', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('medium')).toBeInTheDocument();
      expect(screen.getByText('complex')).toBeInTheDocument();
    });
  });

  it('shows empty state for no search results', async () => {
    vi.mocked(workflowEngineApi.listTemplates).mockResolvedValue({ items: [], total: 0 });
    renderPage();

    const search = screen.getByRole('searchbox');
    fireEvent.change(search, { target: { value: 'nonexistent template' } });

    await waitFor(() => {
      expect(screen.getByRole('status')).toBeInTheDocument();
    });
  });

  it('forks a template, shows the pending spinner, and navigates to the editor', async () => {
    let resolveFork: (wf: unknown) => void = () => {};
    vi.mocked(workflowEngineApi.forkTemplate).mockReturnValue(
      new Promise((resolve) => {
        resolveFork = resolve;
      })
    );

    renderPage();
    await waitFor(() => {
      expect(screen.getByText('KYC Automation')).toBeInTheDocument();
    });

    const forkButtons = screen.getAllByRole('button', { name: /fork template/i });
    fireEvent.click(forkButtons[0]);

    // While the mutation is pending, the fork button for that card should
    // render the spinner instead of the GitFork icon.
    await waitFor(() => {
      expect(document.querySelector('.animate-spin')).toBeTruthy();
    });

    resolveFork({
      id: 'wf-new', name: 'KYC Automation (copy)', status: 'draft' as const,
      version: '1', labels: {}, description: '', created_at: '', updated_at: '',
    });

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/workflows/wf-new/edit');
    });
    expect(workflowEngineApi.forkTemplate).toHaveBeenCalledWith('kyc-automation');

    // Once settled, the spinner should be gone again.
    await waitFor(() => {
      expect(document.querySelector('.animate-spin')).toBeFalsy();
    });
  });

  it('filters by category and resets back to all', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /financial services/i })).toBeInTheDocument();
    });

    const financialButton = screen.getByRole('button', { name: /financial services/i });
    fireEvent.click(financialButton);
    expect(financialButton).toHaveAttribute('aria-pressed', 'true');

    const allButton = screen.getByRole('button', { name: 'All' });
    fireEvent.click(allButton);
    expect(allButton).toHaveAttribute('aria-pressed', 'true');
    expect(financialButton).toHaveAttribute('aria-pressed', 'false');
  });
});
