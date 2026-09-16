import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { StrategicAdvisorPage } from './StrategicAdvisorPage';

const BRIEF = {
  org_id: 'org-1',
  org_name: 'Acme AI',
  week_ending: '2026-01-04',
  health_summary: 'Healthy — on track',
  accomplishments: ['Closed 3 missions', 'Shipped the gateway'],
  risks: ['Budget nearing cap'],
  opportunities: ['Expand into EU'],
  recommendations: ['Hire a compliance agent'],
  generation_method: 'llm',
  generated_at: '2026-01-04T18:00:00Z',
};

function mockBrief(brief: unknown = BRIEF) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/brief/strategic'))
      return new Response(JSON.stringify(brief), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><StrategicAdvisorPage orgId="org-1" orgName="Acme AI" /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('StrategicAdvisorPage', () => {
  test('renders the health summary, section items and the AI-generated badge', async () => {
    mockBrief();
    renderPage();
    expect(await screen.findByText('Healthy — on track')).toBeInTheDocument();
    expect(screen.getByText('AI-generated')).toBeInTheDocument();
    expect(screen.getByText('Accomplishments')).toBeInTheDocument();
    expect(screen.getByText('Closed 3 missions')).toBeInTheDocument();
    expect(screen.getByText('Budget nearing cap')).toBeInTheDocument();
    expect(screen.getByText('Hire a compliance agent')).toBeInTheDocument();
  });

  test('shows the generating state while the brief request is in flight', () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise(() => {}));
    renderPage();
    // Rendered both in the visible loading panel and the sr-only aria-live region.
    expect(screen.getAllByText('Generating strategic brief…').length).toBeGreaterThan(0);
  });

  test('clicking Regenerate re-fetches the strategic brief', async () => {
    const spy = mockBrief();
    renderPage();
    await screen.findByText('Healthy — on track');
    const before = spy.mock.calls.filter(([u]) => String(u).includes('/brief/strategic')).length;
    await userEvent.click(screen.getByRole('button', { name: /Regenerate strategic brief/i }));
    await waitFor(() =>
      expect(spy.mock.calls.filter(([u]) => String(u).includes('/brief/strategic')).length).toBeGreaterThan(before),
    );
  });

  test('renders the no-brief fallback when the request fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'unavailable' }), { status: 503, headers: { 'Content-Type': 'application/json' } }),
    );
    renderPage();
    expect(await screen.findByText(/No brief available/i, {}, { timeout: 5000 })).toBeInTheDocument();
  });
});
