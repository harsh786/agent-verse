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

  test('clearing the emergency stop still resets local state when the DELETE request fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('network down'));
    useEmergencyStore.setState({
      isActive: true,
      activatedAt: new Date().toISOString(),
      cancelledGoals: 5,
      rejectedApprovals: 0,
    });
    renderLayout();

    fireEvent.click(screen.getByRole('button', { name: /Clear Emergency Stop/i }));

    await waitFor(() => {
      expect(useEmergencyStore.getState().isActive).toBe(false);
    });
  });

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
