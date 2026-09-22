/**
 * Tests for WorkflowSettingsPage — 6-panel settings UI.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import WorkflowSettingsPage from '../WorkflowSettingsPage';

vi.mock('../../../lib/api/client', () => ({
  workflowEngineApi: {
    get: vi.fn(),
    list: vi.fn(),
    update: vi.fn(),
  },
}));

import { workflowEngineApi } from '../../../lib/api/client';

const mockWf = {
  id: 'wf-1', name: 'Test Workflow', status: 'draft',
  version: '1', labels: {}, description: 'A test workflow',
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
};

function wrap(wfId = 'wf-1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/workflows/${wfId}/settings`]}>
        <Routes>
          <Route path="/workflows/:id/settings" element={<WorkflowSettingsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('WorkflowSettingsPage', () => {
  beforeEach(() => {
    vi.mocked(workflowEngineApi.get).mockResolvedValue(mockWf as any);
    vi.mocked(workflowEngineApi.list).mockResolvedValue({
      items: [], total: 0, page: 1, per_page: 20,
    });
    vi.mocked(workflowEngineApi.update).mockResolvedValue(mockWf as any);
  });

  it('renders settings heading', async () => {
    wrap();
    await waitFor(() => {
      // The page title includes 'Settings' along with workflow name
      const headings = screen.getAllByText(/settings/i);
      expect(headings.length).toBeGreaterThan(0);
    });
  });

  it('shows settings panel navigation', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByRole('navigation', { name: /settings panels/i })).toBeInTheDocument();
    });
  });

  it('shows all 6 panel options in nav', async () => {
    wrap();
    await waitFor(() => {
      const panels = ['General', 'Permissions', 'Secrets', 'Environment', 'Notifications', 'Webhook'];
      for (const panel of panels) {
        expect(screen.getByRole('button', { name: new RegExp(panel, 'i') })).toBeInTheDocument();
      }
    });
  });

  it('general panel is shown by default', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByText('General Settings')).toBeInTheDocument();
    });
  });

  it('shows workflow name input in general panel', async () => {
    wrap();
    await waitFor(() => {
      const nameInput = screen.getByLabelText(/workflow name/i);
      expect(nameInput).toBeInTheDocument();
      expect((nameInput as HTMLInputElement).value).toBe('Test Workflow');
    });
  });

  it('switching to permissions panel shows permissions content', async () => {
    wrap();
    await waitFor(() => screen.getByText('General Settings'));
    const permBtn = screen.getByRole('button', { name: /permissions/i });
    fireEvent.click(permBtn);
    await waitFor(() => {
      expect(screen.getByText(/access control/i)).toBeInTheDocument();
    });
  });

  it('switching to secrets panel shows secrets content', async () => {
    wrap();
    await waitFor(() => screen.getByText('General Settings'));
    const secretsBtn = screen.getByRole('button', { name: /secrets/i });
    fireEvent.click(secretsBtn);
    await waitFor(() => {
      expect(screen.getByText(/secret references/i)).toBeInTheDocument();
    });
  });

  it('switching to webhook panel shows webhook content', async () => {
    wrap();
    await waitFor(() => screen.getByText('General Settings'));
    const webhookBtn = screen.getByRole('button', { name: /webhook/i });
    fireEvent.click(webhookBtn);
    await waitFor(() => {
      expect(screen.getByText(/webhook trigger/i)).toBeInTheDocument();
    });
  });

  it('shows workflow name in heading', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByText(/Test Workflow/)).toBeInTheDocument();
    });
  });

  it('has save button in general panel', async () => {
    wrap();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /save changes/i })).toBeInTheDocument();
    });
  });

  it('has back link to builder', async () => {
    wrap();
    await new Promise(r => setTimeout(r, 100));
    const links = screen.getAllByRole('link');
    const builderLink = links.find(l =>
      l.getAttribute('href')?.includes('/edit') ||
      l.getAttribute('aria-label')?.toLowerCase().includes('back')
    );
    expect(builderLink).toBeDefined();
  });

  it('switching to environment panel shows environment content', async () => {
    wrap();
    await waitFor(() => screen.getByText('General Settings'));
    fireEvent.click(screen.getByRole('button', { name: /environment/i }));
    await waitFor(() => {
      expect(screen.getByText('Environment Variables')).toBeInTheDocument();
      expect(screen.getByText(/Workflow ID: wf-1 \| Version: 1/)).toBeInTheDocument();
    });
  });

  it('switching to notifications panel shows notification content', async () => {
    wrap();
    await waitFor(() => screen.getByText('General Settings'));
    fireEvent.click(screen.getByRole('button', { name: /notifications/i }));
    await waitFor(() => {
      expect(screen.getByText('Notification Rules')).toBeInTheDocument();
    });
  });

  it('permissions panel shows a loading indicator before resolving', async () => {
    let resolveList!: (v: { items: never[]; total: number; page: number; per_page: number }) => void;
    vi.mocked(workflowEngineApi.list).mockReturnValueOnce(
      new Promise((resolve) => { resolveList = resolve; })
    );
    wrap();
    await waitFor(() => screen.getByText('General Settings'));
    fireEvent.click(screen.getByRole('button', { name: /permissions/i }));
    await waitFor(() => {
      expect(screen.getByText(/loading permissions/i)).toBeInTheDocument();
    });
    resolveList({ items: [], total: 0, page: 1, per_page: 20 });
    await waitFor(() => {
      expect(screen.getByText(/access control/i)).toBeInTheDocument();
    });
  });

  it('marks the active panel button with aria-current', async () => {
    wrap();
    await waitFor(() => screen.getByText('General Settings'));
    const generalBtn = screen.getByRole('button', { name: /general/i });
    expect(generalBtn).toHaveAttribute('aria-current', 'page');
    const secretsBtn = screen.getByRole('button', { name: /secrets/i });
    expect(secretsBtn).not.toHaveAttribute('aria-current');
    fireEvent.click(secretsBtn);
    await waitFor(() => expect(secretsBtn).toHaveAttribute('aria-current', 'page'));
    expect(generalBtn).not.toHaveAttribute('aria-current');
  });

  it('editing the name and description fields updates their values', async () => {
    wrap();
    await waitFor(() => screen.getByText('General Settings'));
    const nameInput = screen.getByLabelText(/workflow name/i) as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: 'Renamed Workflow' } });
    expect(nameInput.value).toBe('Renamed Workflow');

    const descInput = screen.getByLabelText(/description/i) as HTMLTextAreaElement;
    fireEvent.change(descInput, { target: { value: 'New description' } });
    expect(descInput.value).toBe('New description');

    const retentionInput = screen.getByLabelText(/run retention/i) as HTMLInputElement;
    fireEvent.change(retentionInput, { target: { value: '30' } });
    expect(retentionInput.value).toBe('30');
  });

  it('clicking Save Changes calls the update API with the current name/description', async () => {
    wrap();
    await waitFor(() => screen.getByText('General Settings'));
    const nameInput = screen.getByLabelText(/workflow name/i);
    fireEvent.change(nameInput, { target: { value: 'Renamed Workflow' } });

    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));
    await waitFor(() => {
      expect(workflowEngineApi.update).toHaveBeenCalledWith('wf-1', {
        name: 'Renamed Workflow',
        description: 'A test workflow',
      });
    });
  });

  it('shows a full-page loading spinner while the workflow query is pending', () => {
    vi.mocked(workflowEngineApi.get).mockReturnValue(new Promise(() => {}));
    wrap();
    expect(screen.queryByText('General Settings')).not.toBeInTheDocument();
  });
});
