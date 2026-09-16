import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useUiStore } from '@/stores/ui';
import { TopBar } from './TopBar';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => mockNavigate };
});

function renderTopBar() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function mockFetch(overrides: Partial<Record<'goals' | 'agents' | 'connectors' | 'approvals', unknown>> = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    const json = (body: unknown) =>
      new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/goals')) return json(overrides.goals ?? { goals: [] });
    if (url.includes('/agents')) return json(overrides.agents ?? []);
    if (url.includes('/connectors')) return json(overrides.connectors ?? []);
    if (url.includes('/governance/approvals')) return json(overrides.approvals ?? []);
    return json({});
  });
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  mockNavigate.mockReset();
  useAuthStore.setState({ apiKey: 'k', tenantId: 'tenant-xyz', plan: 'pro', isAuthenticated: true });
  useUiStore.setState({ sidebarOpen: true, commandPaletteOpen: false, theme: 'light' });
});
afterEach(() => vi.restoreAllMocks());

describe('TopBar', () => {
  test('renders the plan badge and tenant id', () => {
    mockFetch();
    renderTopBar();
    expect(screen.getByText('pro')).toBeInTheDocument();
    expect(screen.getByText('tenant-xyz')).toBeInTheDocument();
  });

  test('renders the global search input and command palette shortcut', () => {
    mockFetch();
    renderTopBar();
    expect(screen.getByLabelText('Global search')).toBeInTheDocument();
    expect(screen.getByLabelText('Open command palette')).toBeInTheDocument();
  });

  test('opens the command palette when the shortcut button is clicked', () => {
    mockFetch();
    renderTopBar();
    fireEvent.click(screen.getByLabelText('Open command palette'));
    expect(useUiStore.getState().commandPaletteOpen).toBe(true);
  });

  test('toggles the sidebar via the mobile hamburger button', () => {
    mockFetch();
    renderTopBar();
    fireEvent.click(screen.getByLabelText('Toggle sidebar'));
    expect(useUiStore.getState().sidebarOpen).toBe(false);
  });

  test('shows the moon icon and "Switch to dark mode" label in light theme', () => {
    mockFetch();
    renderTopBar();
    expect(screen.getByLabelText('Switch to dark mode')).toBeInTheDocument();
  });

  test('shows the sun icon and "Switch to light mode" label in dark theme', () => {
    mockFetch();
    useUiStore.setState({ theme: 'dark' });
    renderTopBar();
    expect(screen.getByLabelText('Switch to light mode')).toBeInTheDocument();
  });

  test('toggles the theme when the theme button is clicked', () => {
    mockFetch();
    renderTopBar();
    fireEvent.click(screen.getByLabelText('Switch to dark mode'));
    expect(useUiStore.getState().theme).toBe('dark');
  });

  test('logs out and navigates to /auth when sign out is clicked', () => {
    mockFetch();
    renderTopBar();
    fireEvent.click(screen.getByLabelText('Sign out'));
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
    expect(mockNavigate).toHaveBeenCalledWith('/auth');
  });

  test('renders the language switcher', () => {
    mockFetch();
    renderTopBar();
    expect(screen.getByRole('group', { name: 'Language selection' })).toBeInTheDocument();
  });

  test('debounces search input and shows matching results in a dropdown', async () => {
    vi.useFakeTimers();
    mockFetch({
      goals: { goals: [{ id: 'g1', goal_id: 'g1', goal: 'Launch marketing site', status: 'executing' }] },
      agents: [{ agent_id: 'a1', name: 'Launch Bot', autonomy_mode: 'L3' }],
      connectors: [{ server_id: 'c1', name: 'Slack Launch', status: 'active' }],
    });
    renderTopBar();

    const input = screen.getByLabelText('Global search');
    fireEvent.change(input, { target: { value: 'launch' } });

    await vi.advanceTimersByTimeAsync(350);
    vi.useRealTimers();

    await waitFor(() => expect(screen.getByText('Launch marketing site')).toBeInTheDocument());
    expect(screen.getByText('Launch Bot')).toBeInTheDocument();
    expect(screen.getByText('Slack Launch')).toBeInTheDocument();
  });

  test('clears results when the query is emptied', async () => {
    vi.useFakeTimers();
    mockFetch({ goals: { goals: [{ id: 'g1', goal_id: 'g1', goal: 'Launch marketing site', status: 'executing' }] } });
    renderTopBar();

    const input = screen.getByLabelText('Global search');
    fireEvent.change(input, { target: { value: 'launch' } });
    await vi.advanceTimersByTimeAsync(350);
    vi.useRealTimers();

    await waitFor(() => expect(screen.getByText('Launch marketing site')).toBeInTheDocument());

    fireEvent.change(input, { target: { value: '' } });
    vi.useFakeTimers();
    await vi.advanceTimersByTimeAsync(350);
    vi.useRealTimers();
    await waitFor(() => expect(screen.queryByText('Launch marketing site')).not.toBeInTheDocument());
  });

  test('selecting a goal result navigates to the goal detail page and clears the search', async () => {
    vi.useFakeTimers();
    mockFetch({
      goals: { goals: [{ id: 'g1', goal_id: 'g1', goal: 'Launch marketing site', status: 'executing' }] },
    });
    renderTopBar();

    const input = screen.getByLabelText('Global search');
    fireEvent.change(input, { target: { value: 'launch' } });
    await vi.advanceTimersByTimeAsync(350);
    vi.useRealTimers();

    await waitFor(() => expect(screen.getByText('Launch marketing site')).toBeInTheDocument());
    fireEvent.mouseDown(screen.getByText('Launch marketing site'));

    expect(mockNavigate).toHaveBeenCalledWith('/goals/g1');
    expect((input as HTMLInputElement).value).toBe('');
  });

  test('selecting an agent result navigates to the agent detail page', async () => {
    vi.useFakeTimers();
    mockFetch({ agents: [{ agent_id: 'a1', name: 'Launch Bot', autonomy_mode: 'L3' }] });
    renderTopBar();

    fireEvent.change(screen.getByLabelText('Global search'), { target: { value: 'launch' } });
    await vi.advanceTimersByTimeAsync(350);
    vi.useRealTimers();

    await waitFor(() => expect(screen.getByText('Launch Bot')).toBeInTheDocument());
    fireEvent.mouseDown(screen.getByText('Launch Bot'));
    expect(mockNavigate).toHaveBeenCalledWith('/agents/a1');
  });

  test('selecting a connector result navigates to the connector detail page', async () => {
    vi.useFakeTimers();
    mockFetch({ connectors: [{ server_id: 'c1', name: 'Slack Launch', status: 'active' }] });
    renderTopBar();

    fireEvent.change(screen.getByLabelText('Global search'), { target: { value: 'launch' } });
    await vi.advanceTimersByTimeAsync(350);
    vi.useRealTimers();

    await waitFor(() => expect(screen.getByText('Slack Launch')).toBeInTheDocument());
    fireEvent.mouseDown(screen.getByText('Slack Launch'));
    expect(mockNavigate).toHaveBeenCalledWith('/connectors/c1');
  });

  test('does not search when the query is blank (whitespace only)', async () => {
    vi.useFakeTimers();
    const fetchSpy = mockFetch();
    renderTopBar();

    fireEvent.change(screen.getByLabelText('Global search'), { target: { value: '   ' } });
    await vi.advanceTimersByTimeAsync(350);
    vi.useRealTimers();

    // Only the initial PendingApprovalsBadge poll should have hit fetch, not the search endpoints.
    const searchCalls = fetchSpy.mock.calls.filter(([input]) => String(input).includes('/goals?limit'));
    expect(searchCalls).toHaveLength(0);
  });

  test('does not search when there is no apiKey', async () => {
    vi.useFakeTimers();
    const fetchSpy = mockFetch();
    useAuthStore.setState({ apiKey: '' });
    renderTopBar();

    fireEvent.change(screen.getByLabelText('Global search'), { target: { value: 'launch' } });
    await vi.advanceTimersByTimeAsync(350);
    vi.useRealTimers();

    const searchCalls = fetchSpy.mock.calls.filter(([input]) => String(input).includes('/goals?limit'));
    expect(searchCalls).toHaveLength(0);
  });

  test('clears results and hides the dropdown when the search request throws', async () => {
    vi.useFakeTimers();
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('network down'));
    renderTopBar();

    fireEvent.change(screen.getByLabelText('Global search'), { target: { value: 'launch' } });
    await vi.advanceTimersByTimeAsync(350);
    vi.useRealTimers();

    await waitFor(() => expect(screen.queryByText(/Launch/)).not.toBeInTheDocument());
  });

  test('closing on blur hides the dropdown', async () => {
    vi.useFakeTimers();
    mockFetch({ agents: [{ agent_id: 'a1', name: 'Launch Bot', autonomy_mode: 'L3' }] });
    renderTopBar();

    const input = screen.getByLabelText('Global search');
    fireEvent.change(input, { target: { value: 'launch' } });
    await vi.advanceTimersByTimeAsync(350);
    vi.useRealTimers();

    await waitFor(() => expect(screen.getByText('Launch Bot')).toBeInTheDocument());

    vi.useFakeTimers();
    fireEvent.blur(input);
    await vi.advanceTimersByTimeAsync(200);
    vi.useRealTimers();

    await waitFor(() => expect(screen.queryByText('Launch Bot')).not.toBeInTheDocument());
  });
});
