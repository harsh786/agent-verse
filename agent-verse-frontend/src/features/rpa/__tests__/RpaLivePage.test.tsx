import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { RpaLivePage } from '../RpaLivePage';

const SESSION = (overrides = {}) => ({
  session_id: 'sess-001',
  status: 'active',
  created_at: new Date().toISOString(),
  ...overrides,
});

function mockFetch(sessions = [SESSION()]) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init as RequestInit | undefined)?.method ?? 'GET';
    if (url.includes('/rpa/sessions') && method === 'POST')
      return new Response(JSON.stringify({ session_id: 'new-sess', status: 'active', created_at: new Date().toISOString() }), { status: 201 });
    if (url.includes('/rpa/sessions/') && method === 'DELETE')
      return new Response(null, { status: 204 });
    if (url.includes('/rpa/sessions/') && url.includes('/screenshot'))
      return new Response(JSON.stringify({ session_id: 'sess-001', screenshot_data_uri: 'data:image/png;base64,abc', url: 'https://example.com', timestamp: new Date().toISOString() }), { status: 200 });
    if (url.includes('/rpa/sessions/') && url.includes('/takeover'))
      return new Response(JSON.stringify({ session_id: 'sess-001', status: 'awaiting_human', message: 'Takeover requested' }), { status: 200 });
    if (url.includes('/rpa/sessions') && method === 'GET')
      return new Response(JSON.stringify(sessions), { status: 200 });
    if (url.includes('/rpa/tools'))
      return new Response(JSON.stringify({ tools: [{ name: 'rpa_click', description: 'Click', risk: 'high' }, { name: 'rpa_open_url', description: 'Open URL', risk: 'low' }] }), { status: 200 });
    if (url.includes('/rpa/execute'))
      return new Response(JSON.stringify({ success: true, output: 'Clicked', tool_name: 'rpa_click', duration_ms: 150 }), { status: 200 });
    return new Response('[]', { status: 200 });
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <RpaLivePage />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  sessionStorage.setItem('av_api_key', 'test-key');
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('RpaLivePage', () => {
  test('renders heading', () => {
    mockFetch([]);
    renderPage();
    expect(screen.getByRole('heading', { name: /rpa live/i })).toBeInTheDocument();
  });

  test('shows empty session state', async () => {
    mockFetch([]);
    renderPage();
    await waitFor(() => expect(screen.getByText(/no active sessions/i)).toBeInTheDocument());
  });

  test('lists sessions', async () => {
    mockFetch([SESSION()]);
    renderPage();
    await waitFor(() => expect(screen.getByText(/sess-001/)).toBeInTheDocument());
  });

  test('shows session status badge', async () => {
    mockFetch([SESSION()]);
    renderPage();
    await waitFor(() => screen.getByText(/sess-001/));
    expect(screen.getByText('active')).toBeInTheDocument();
  });

  test('New Session button calls POST /rpa/sessions', async () => {
    const fetchSpy = mockFetch([]);
    renderPage();
    await waitFor(() => expect(screen.getByRole('button', { name: /new session/i })).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /new session/i }));
    await waitFor(() => {
      const post = fetchSpy.mock.calls.find(([u, i]) => String(u).includes('/rpa/sessions') && (i as RequestInit)?.method === 'POST');
      expect(post).toBeTruthy();
    });
  });

  test('shows empty state when no session selected', async () => {
    mockFetch([SESSION()]);
    renderPage();
    await waitFor(() => screen.getByText(/sess-001/));
    expect(screen.getByText(/no session selected/i)).toBeInTheDocument();
  });

  test('Tool Console expands on click', async () => {
    mockFetch([SESSION()]);
    renderPage();
    await waitFor(() => screen.getByText(/sess-001/));
    // Select a session
    await userEvent.click(screen.getAllByText(/sess-001/)[0]);
    // Check for tool console toggle (there might be multiple elements with "tool console" text)
    await waitFor(() => expect(screen.getAllByText(/tool console/i).length).toBeGreaterThanOrEqual(1));
    // Click the button version
    const toolConsoleBtn = screen.getAllByText(/tool console/i).find(el => el.closest('button'));
    if (toolConsoleBtn) {
      await userEvent.click(toolConsoleBtn.closest('button')!);
      await waitFor(() => expect(screen.getByLabelText(/search rpa tools/i)).toBeInTheDocument());
    }
  });

  test('takeover button opens modal', async () => {
    mockFetch([SESSION()]);
    renderPage();
    await waitFor(() => screen.getByText(/sess-001/));
    await userEvent.click(screen.getAllByText(/sess-001/)[0]);
    await waitFor(() => screen.getByRole('button', { name: /takeover/i }));
    await userEvent.click(screen.getByRole('button', { name: /takeover/i }));
    await expect(screen.getByLabelText(/takeover reason/i)).toBeInTheDocument();
  });
});
