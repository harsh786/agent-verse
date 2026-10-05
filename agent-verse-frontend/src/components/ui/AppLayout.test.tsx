import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useUiStore } from '@/stores/ui';
import { useEmergencyStore } from '@/stores/emergency';
import { AppLayout } from './AppLayout';

function mockFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    // The sidebar + TopBar badge both poll pending approvals.
    if (url.includes('/governance/approvals'))
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderLayout(initialPath = '/dashboard') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/" element={<AppLayout />}>
            <Route path="dashboard" element={<div>outlet-marker-content</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 'tenant-xyz', plan: 'free', isAuthenticated: true });
  // Keep the sidebar expanded so nav labels render as text.
  useUiStore.setState({ sidebarOpen: true, commandPaletteOpen: false });
  useEmergencyStore.setState({ isActive: false, activatedAt: null, cancelledGoals: 0, rejectedApprovals: 0 });
});
afterEach(() => vi.restoreAllMocks());

function mockStopState(active: boolean) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes('/governance/emergency-stop') && (!init?.method || init.method === 'GET'))
      return new Response(
        JSON.stringify({ active, activated_at: active ? '2026-09-29T10:00:00+00:00' : null }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      );
    return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

describe('AppLayout emergency banner follows the server', () => {
  test('a failing status read shows "status unknown", never a remembered local value', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      if (String(input).includes('/governance/emergency-stop') && (!init?.method || init.method === 'GET'))
        return new Response(JSON.stringify({ detail: 'control store unavailable' }), {
          status: 503, headers: { 'Content-Type': 'application/json' },
        });
      return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    // A stale "active, 7 cancelled" from an earlier session in this browser.
    useEmergencyStore.setState({ isActive: true, activatedAt: '2026-01-01T00:00:00Z', cancelledGoals: 7, rejectedApprovals: 0 });
    renderLayout();
    expect(await screen.findByText(/Emergency-stop status unknown/i)).toBeInTheDocument();
    expect(screen.queryByText(/7 goals cancelled/i)).not.toBeInTheDocument();
  });

  test('the cancelled-goals count comes from the server (a stop activated elsewhere)', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      if (String(input).includes('/governance/emergency-stop') && (!init?.method || init.method === 'GET'))
        return new Response(
          JSON.stringify({ active: true, activated_at: '2026-09-29T10:00:00+00:00', cancelled_goals: 5, rejected_approvals: 2 }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderLayout();
    expect(await screen.findByText(/5 goals cancelled/i)).toBeInTheDocument();
  });

  test('shows the banner when the server reports an active stop', async () => {
    mockStopState(true);
    renderLayout();
    expect(await screen.findByText(/Emergency Stop Active/i)).toBeInTheDocument();
    expect(useEmergencyStore.getState().isActive).toBe(true);
  });

  test('drops a stale local banner when the server reports no stop', async () => {
    mockStopState(false);
    useEmergencyStore.setState({
      isActive: true,
      activatedAt: new Date().toISOString(),
      cancelledGoals: 3,
      rejectedApprovals: 0,
    });
    renderLayout();
    await waitFor(() => expect(useEmergencyStore.getState().isActive).toBe(false));
    expect(screen.queryByText(/Emergency Stop Active/i)).not.toBeInTheDocument();
  });
});

describe('AppLayout', () => {
  test('renders the sidebar navigation labels', () => {
    mockFetch();
    renderLayout();
    expect(screen.getByRole('link', { name: 'Dashboard' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Chat Agents' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Graphify' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Organizations' })).toBeInTheDocument();
  });

  test('renders the routed outlet content', () => {
    mockFetch();
    renderLayout();
    expect(screen.getByText('outlet-marker-content')).toBeInTheDocument();
  });

  test('marks the current route link as active (aria-current="page")', () => {
    mockFetch();
    renderLayout('/dashboard');
    expect(screen.getByRole('link', { name: 'Dashboard' })).toHaveAttribute('aria-current', 'page');
    // A non-active link does not carry aria-current.
    expect(screen.getByRole('link', { name: 'Graphify' })).not.toHaveAttribute('aria-current');
  });

  test('hides the emergency banner while emergency stop is inactive', () => {
    mockFetch();
    renderLayout();
    expect(screen.queryByText(/Emergency Stop Active/i)).not.toBeInTheDocument();
  });

  test('shows the emergency banner when the emergency store is active', () => {
    mockFetch();
    useEmergencyStore.setState({
      isActive: true,
      activatedAt: new Date().toISOString(),
      cancelledGoals: 3,
      rejectedApprovals: 1,
    });
    renderLayout();
    expect(screen.getByText(/Emergency Stop Active/i)).toBeInTheDocument();
    expect(screen.getByText(/3 goals cancelled\./i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Clear Emergency Stop/i })).toBeInTheDocument();
  });

  test('exposes the skip-to-main-content accessibility link', () => {
    mockFetch();
    renderLayout();
    expect(screen.getByRole('link', { name: 'Skip to main content' })).toBeInTheDocument();
  });

  test('clicking the mobile backdrop closes the sidebar', () => {
    mockFetch();
    const { container } = renderLayout();
    const backdrop = container.querySelector('[aria-hidden="true"].fixed.inset-0.z-20');
    expect(backdrop).toBeTruthy();
    fireEvent.click(backdrop as Element);
    expect(useUiStore.getState().sidebarOpen).toBe(false);
  });

  test('does not render the mobile backdrop when the sidebar is closed', () => {
    mockFetch();
    useUiStore.setState({ sidebarOpen: false });
    const { container } = renderLayout();
    expect(container.querySelector('.fixed.inset-0.z-20')).toBeNull();
  });

  test('clearing the emergency stop calls the DELETE endpoint and resets local state', async () => {
    const fetchSpy = mockFetch();
    useEmergencyStore.setState({
      isActive: true,
      activatedAt: new Date().toISOString(),
      cancelledGoals: 2,
      rejectedApprovals: 0,
    });
    renderLayout();

    fireEvent.click(screen.getByRole('button', { name: /Clear Emergency Stop/i }));

    await waitFor(() => {
      expect(useEmergencyStore.getState().isActive).toBe(false);
    });
    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining('/governance/emergency-stop'),
      expect.objectContaining({ method: 'DELETE' }),
    );
    expect(screen.queryByText(/Emergency Stop Active/i)).not.toBeInTheDocument();
  });

  test('a network failure clearing the stop keeps the banner and says so', async () => {
    // Regression: the banner claimed the stop was lifted even when the DELETE
    // never reached the server (a false UI state for a safety control). The
    // status read still succeeds (a failing read is the "status unknown" case).
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      if (String(input).includes('/governance/emergency-stop') && init?.method === 'DELETE')
        throw new Error('network down');
      if (String(input).includes('/governance/emergency-stop'))
        return new Response(
          JSON.stringify({ active: true, activated_at: '2026-09-29T10:00:00+00:00', cancelled_goals: 5 }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    useEmergencyStore.setState({
      isActive: true,
      activatedAt: new Date().toISOString(),
      cancelledGoals: 5,
      rejectedApprovals: 0,
    });
    renderLayout();

    fireEvent.click(screen.getByRole('button', { name: /Clear Emergency Stop/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not be cleared.*network down/i);
    expect(useEmergencyStore.getState().isActive).toBe(true);
    expect(screen.getByText(/Emergency Stop Active/i)).toBeInTheDocument();
  });

  test.each([401, 403, 500, 503])(
    'a %i from DELETE /governance/emergency-stop keeps the stop shown as active',
    async (status) => {
      vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
        if (String(input).includes('/governance/emergency-stop') && init?.method === 'DELETE')
          return new Response(JSON.stringify({ detail: 'Admin role required' }), {
            status,
            headers: { 'Content-Type': 'application/json' },
          });
        return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
      });
      useEmergencyStore.setState({
        isActive: true,
        activatedAt: new Date().toISOString(),
        cancelledGoals: 1,
        rejectedApprovals: 0,
      });
      renderLayout();

      fireEvent.click(screen.getByRole('button', { name: /Clear Emergency Stop/i }));

      expect(await screen.findByRole('alert')).toHaveTextContent(/could not be cleared/i);
      expect(useEmergencyStore.getState().isActive).toBe(true);
    },
  );

  test('opens the keyboard-shortcuts help overlay via "shift+/" and closes it on Escape', () => {
    mockFetch();
    renderLayout();

    expect(screen.queryByText('Keyboard Shortcuts')).not.toBeInTheDocument();
    fireEvent.keyDown(document.body, { key: '/', code: '/', shiftKey: true });
    expect(screen.getByText('Keyboard Shortcuts')).toBeInTheDocument();

    fireEvent.keyDown(document.body, { key: 'Escape', code: 'Escape' });
    expect(screen.queryByText('Keyboard Shortcuts')).not.toBeInTheDocument();
  });

  test('closes the help overlay via the Close button and via the backdrop, not via an inner click', () => {
    mockFetch();
    renderLayout();
    fireEvent.keyDown(document.body, { key: '/', code: '/', shiftKey: true });
    expect(screen.getByText('Keyboard Shortcuts')).toBeInTheDocument();

    // Clicking inside the modal panel must not close it (stopPropagation).
    fireEvent.click(screen.getByText('Keyboard Shortcuts'));
    expect(screen.getByText('Keyboard Shortcuts')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByText('Keyboard Shortcuts')).not.toBeInTheDocument();
  });

  test('closes the help overlay when clicking the outer backdrop', () => {
    mockFetch();
    const { container } = renderLayout();
    fireEvent.keyDown(document.body, { key: '/', code: '/', shiftKey: true });
    expect(screen.getByText('Keyboard Shortcuts')).toBeInTheDocument();

    const overlay = container.querySelector('.fixed.inset-0.z-\\[150\\]');
    expect(overlay).toBeTruthy();
    fireEvent.click(overlay as Element);
    expect(screen.queryByText('Keyboard Shortcuts')).not.toBeInTheDocument();
  });

  test('falls back to "now" in the emergency banner when activatedAt is not set', () => {
    mockFetch();
    useEmergencyStore.setState({
      isActive: true,
      activatedAt: null,
      cancelledGoals: 0,
      rejectedApprovals: 0,
    });
    renderLayout();
    expect(screen.getByText(/since now\./i)).toBeInTheDocument();
  });
});
