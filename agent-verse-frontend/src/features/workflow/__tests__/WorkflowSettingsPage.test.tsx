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
});
