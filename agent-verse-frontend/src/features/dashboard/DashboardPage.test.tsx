import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { DashboardPage } from './DashboardPage';

function renderDashboardPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('DashboardPage', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('av_api_key', 'tenant-key');
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'free',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders all four KPI card labels', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderDashboardPage();
    expect(screen.getByText('Active Goals')).toBeInTheDocument();
    expect(screen.getByText('Success Rate')).toBeInTheDocument();
    expect(screen.getByText('Avg Latency')).toBeInTheDocument();
    expect(screen.getByText('Cost Today')).toBeInTheDocument();
  });

  test('shows loading dashes before data arrives', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderDashboardPage();
    // Active Goals and Success Rate show "—" during load; Avg Latency is always "—"
    const dashes = screen.getAllByText('—');
    expect(dashes.length).toBeGreaterThanOrEqual(3);
  });

  test('shows page title and subtitle', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderDashboardPage();
    expect(screen.getByText('Dashboard')).toBeInTheDocument();
    expect(screen.getByText(/real-time platform overview/i)).toBeInTheDocument();
  });

  test('renders goal entries in activity feed when goals exist', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          goals: [
            { id: 'g1', goal: 'First test goal', status: 'complete', created_at: new Date().toISOString() },
            { id: 'g2', goal: 'Second test goal', status: 'executing', created_at: new Date().toISOString() },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );
    renderDashboardPage();
    await waitFor(() => expect(screen.getByText('First test goal')).toBeInTheDocument());
    expect(screen.getByText('Second test goal')).toBeInTheDocument();
  });

  test('shows empty-state message when no goals exist', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ goals: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );
    renderDashboardPage();
    await waitFor(() =>
      expect(screen.getByText(/no goals yet/i)).toBeInTheDocument()
    );
  });

  test('computes active goal count from executing and planning goals', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          goals: [
            { id: 'g1', goal: 'Goal A', status: 'executing', created_at: new Date().toISOString() },
            { id: 'g2', goal: 'Goal B', status: 'planning', created_at: new Date().toISOString() },
            { id: 'g3', goal: 'Goal C', status: 'complete', created_at: new Date().toISOString() },
            { id: 'g4', goal: 'Goal D', status: 'failed', created_at: new Date().toISOString() },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );
    renderDashboardPage();
    await waitFor(() => expect(screen.getByText('Goal A')).toBeInTheDocument());
    // active = executing + planning = 2
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  test('renders status badges for goals in the activity feed', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          goals: [
            { id: 'g1', goal: 'Done goal', status: 'complete', created_at: new Date().toISOString() },
            { id: 'g2', goal: 'Running goal', status: 'executing', created_at: new Date().toISOString() },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );
    renderDashboardPage();
    await waitFor(() => expect(screen.getByText('complete')).toBeInTheDocument());
    expect(screen.getByText('executing')).toBeInTheDocument();
  });

  test('renders Live Activity Feed section header', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderDashboardPage();
    expect(screen.getByText('Live Activity Feed')).toBeInTheDocument();
  });
});
