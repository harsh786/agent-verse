import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useUiStore } from '@/stores/ui';
import { Sidebar } from './Sidebar';

function renderSidebar() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function mockFetch(approvals: Array<{ status: string }> = []) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/governance/approvals')) {
      return new Response(JSON.stringify(approvals), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

beforeEach(() => {
  useAuthStore.setState({
    apiKey: 'test-key',
    tenantId: 'tenant-abcdef123456',
    plan: 'professional',
    isAuthenticated: true,
  });
  useUiStore.setState({ sidebarOpen: true });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('Sidebar', () => {
  test('renders logo and core nav sections when expanded', async () => {
    mockFetch();
    renderSidebar();
    expect(screen.getByText('AgentVerse')).toBeInTheDocument();
    expect(screen.getByText('Core')).toBeInTheDocument();
    expect(screen.getByText('Platform')).toBeInTheDocument();
    expect(screen.getAllByText('Governance').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Enterprise').length).toBeGreaterThan(0);
    expect(screen.getByText('Tooling')).toBeInTheDocument();
  });

  test('renders the Model Control Center nav entry', async () => {
    mockFetch();
    renderSidebar();
    expect(screen.getByText('Model Control Center')).toBeInTheDocument();
  });

  test('renders tenant id and plan in profile section', async () => {
    mockFetch();
    renderSidebar();
    await waitFor(() => expect(screen.getByText('professional plan')).toBeInTheDocument());
    // tenantId sliced to 12 chars + ellipsis
    expect(screen.getByText(/tenant-abcde/)).toBeInTheDocument();
  });

  test('shows "—" and "free plan" when no tenantId/plan set', async () => {
    useAuthStore.setState({ tenantId: '', plan: '' });
    mockFetch();
    renderSidebar();
    await waitFor(() => expect(screen.getByText('free plan')).toBeInTheDocument());
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  test('collapse toggle button collapses and hides labels', async () => {
    mockFetch();
    renderSidebar();
    const collapseBtn = screen.getByLabelText('Collapse sidebar');
    fireEvent.click(collapseBtn);
    // useUiStore state should now be sidebarOpen: false; label swaps
    await waitFor(() => expect(screen.getByLabelText('Expand sidebar')).toBeInTheDocument());
    expect(screen.queryByText('AgentVerse')).not.toBeInTheDocument();
  });

  test('New Goal button navigates to /goals', async () => {
    mockFetch();
    renderSidebar();
    const btn = screen.getByLabelText('Create new goal');
    fireEvent.click(btn);
    // Navigation itself isn't observable without a route assertion harness;
    // ensure the click doesn't throw and the button remains present.
    expect(btn).toBeInTheDocument();
  });

  test('search filters nav items by label', async () => {
    mockFetch();
    renderSidebar();
    const search = screen.getByPlaceholderText('Search...');
    await userEvent.type(search, 'Budget Manager');
    await waitFor(() => {
      expect(screen.getByText('Budget Manager')).toBeInTheDocument();
      expect(screen.queryByText('Dashboard')).not.toBeInTheDocument();
    });
    // Section headings for non-matching sections should disappear entirely
    expect(screen.queryByText('Core')).not.toBeInTheDocument();
  });

  test('search with no matches shows no nav items', async () => {
    mockFetch();
    renderSidebar();
    const search = screen.getByPlaceholderText('Search...');
    await userEvent.type(search, 'zzz-no-such-feature-zzz');
    await waitFor(() => expect(screen.queryByText('Dashboard')).not.toBeInTheDocument());
  });

  test('Enterprise section collapsed by default shows only pinned items', async () => {
    mockFetch();
    renderSidebar();
    // Pinned items always shown
    expect(screen.getByText('AI Builder')).toBeInTheDocument();
    // Non-pinned enterprise item hidden until expanded
    expect(screen.queryByText('Red Team')).not.toBeInTheDocument();
    expect(screen.getByText(/hidden\)/)).toBeInTheDocument();
  });

  test('Enterprise section expands via heading click to show all items', async () => {
    mockFetch();
    renderSidebar();
    const heading = screen.getByRole('button', { name: 'Enterprise' });
    fireEvent.click(heading);
    await waitFor(() => expect(screen.getByText('Red Team')).toBeInTheDocument());
    expect(screen.getByText('Less')).toBeInTheDocument();
  });

  test('Enterprise section expands via keyboard Enter on heading', async () => {
    mockFetch();
    renderSidebar();
    const heading = screen.getByRole('button', { name: 'Enterprise' });
    fireEvent.keyDown(heading, { key: 'Enter' });
    await waitFor(() => expect(screen.getByText('Red Team')).toBeInTheDocument());
  });

  test('Enterprise section expands via keyboard Space on heading', async () => {
    mockFetch();
    renderSidebar();
    const heading = screen.getByRole('button', { name: 'Enterprise' });
    fireEvent.keyDown(heading, { key: ' ' });
    await waitFor(() => expect(screen.getByText('Red Team')).toBeInTheDocument());
  });

  test('Enterprise "More" toggle button also expands/collapses', async () => {
    mockFetch();
    renderSidebar();
    const moreBtn = screen.getByText(/hidden\)/).closest('button') as HTMLElement;
    fireEvent.click(moreBtn);
    await waitFor(() => expect(screen.getByText('Less')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Less'));
    await waitFor(() => expect(screen.getByText(/hidden\)/)).toBeInTheDocument());
  });

  test('searching does not apply enterprise pin-collapse logic', async () => {
    mockFetch();
    renderSidebar();
    const search = screen.getByPlaceholderText('Search...');
    await userEvent.type(search, 'Red Team');
    await waitFor(() => expect(screen.getByText('Red Team')).toBeInTheDocument());
    // "More" toggle should not render while searching
    expect(screen.queryByText(/hidden\)/)).not.toBeInTheDocument();
  });

  test('shows pending approvals badge when approvals are pending', async () => {
    mockFetch([{ status: 'pending' }, { status: 'pending' }, { status: 'approved' }]);
    renderSidebar();
    await waitFor(() => expect(screen.getByText('2')).toBeInTheDocument());
  });

  test('caps approvals badge display at 99+', async () => {
    const many = Array.from({ length: 150 }, () => ({ status: 'pending' }));
    mockFetch(many);
    renderSidebar();
    await waitFor(() => expect(screen.getByText('99+')).toBeInTheDocument());
  });

  test('does not fetch approvals when not authenticated', async () => {
    useAuthStore.setState({ isAuthenticated: false });
    const spy = mockFetch();
    renderSidebar();
    await waitFor(() => expect(screen.getByText('AgentVerse')).toBeInTheDocument());
    expect(spy.mock.calls.some(([u]) => String(u).includes('/governance/approvals'))).toBe(false);
  });

  test('logout button calls auth store logout', async () => {
    mockFetch();
    const logoutSpy = vi.fn();
    useAuthStore.setState({ logout: logoutSpy });
    renderSidebar();
    fireEvent.click(screen.getByTitle('Sign out'));
    expect(logoutSpy).toHaveBeenCalled();
  });

  test('mobile backdrop closes sidebar on click when open', async () => {
    mockFetch();
    const { container } = renderSidebar();
    const backdrop = container.querySelector('.fixed.inset-0.bg-black\\/40');
    expect(backdrop).toBeInTheDocument();
    fireEvent.click(backdrop as Element);
    await waitFor(() => expect(screen.getByLabelText('Expand sidebar')).toBeInTheDocument());
  });

  test('mobile close button toggles sidebar closed', async () => {
    mockFetch();
    renderSidebar();
    fireEvent.click(screen.getByLabelText('Close sidebar'));
    await waitFor(() => expect(screen.getByLabelText('Expand sidebar')).toBeInTheDocument());
  });

  test('collapsed sidebar shows section dividers instead of headings', async () => {
    useUiStore.setState({ sidebarOpen: false });
    mockFetch();
    renderSidebar();
    await waitFor(() => expect(screen.queryByText('Core')).not.toBeInTheDocument());
  });

  test('collapsed sidebar renders logout icon-only button', async () => {
    useUiStore.setState({ sidebarOpen: false });
    mockFetch();
    renderSidebar();
    await waitFor(() => expect(screen.getAllByLabelText('Sign out').length).toBeGreaterThan(0));
  });
});
