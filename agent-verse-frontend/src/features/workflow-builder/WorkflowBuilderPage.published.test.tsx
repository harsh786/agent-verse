import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { WorkflowBuilderPage } from './WorkflowBuilderPage';

vi.mock('@/stores/auth', () => {
  const mockState = { apiKey: 'test', ssoMode: false, accessToken: null, tenantId: 'tenant1', plan: 'free', isAuthenticated: true };
  const useAuthStore = (sel: any) => sel(mockState);
  // API client calls useAuthStore.getState() in request()
  (useAuthStore as any).getState = () => ({ ...mockState, logout: vi.fn() });
  // request() also adds the MFA session header; without this export the mock made
  // every API call throw before fetch (the page's catch swallowed it as a toast).
  return { useAuthStore, getMfaHeader: () => ({}) };
});

vi.mock('@/stores/toast', () => ({
  toast: vi.fn(),
}));

const mockFetch = vi.fn().mockResolvedValue({ ok: true, json: async () => [] });
vi.stubGlobal('fetch', mockFetch);

// ── WF-30: a published workflow refuses in-place edits (409) ─────────────────

it('surfaces the 409 reason when saving a published workflow', async () => {
  const { toast } = await import('@/stores/toast');
  const wf = {
    id: 'wf-pub', name: 'Live', definition: { steps: [] }, status: 'published', version: 2,
    created_at: '', updated_at: '', description: '',
  };
  mockFetch.mockReset();
  mockFetch.mockImplementation(async (url: string, init?: { method?: string }) => {
    const method = init?.method ?? 'GET';
    if (method === 'PUT') {
      return {
        ok: false, status: 409,
        json: async () => ({ detail: 'workflow is published; unpublish it before editing' }),
      };
    }
    if (String(url).endsWith('/workflows/wf-pub')) return { ok: true, json: async () => wf };
    return { ok: true, json: async () => [wf] };
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><WorkflowBuilderPage /></MemoryRouter>
    </QueryClientProvider>,
  );
  const picker = await screen.findByLabelText(/Load saved workflow/i, {}, { timeout: 5000 });
  fireEvent.change(picker, { target: { value: 'wf-pub' } });
  await waitFor(() => expect(screen.getByLabelText(/Workflow name/i)).toHaveValue('Live'));
  fireEvent.click(screen.getByRole('button', { name: /Add Trigger \/ Start node/i }));
  fireEvent.click(screen.getByRole('button', { name: /save workflow/i }));
  await waitFor(() =>
    expect(vi.mocked(toast)).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'error', message: expect.stringMatching(/unpublish/) }),
    ),
  );
  mockFetch.mockReset();
  mockFetch.mockResolvedValue({ ok: true, json: async () => [] });
});
