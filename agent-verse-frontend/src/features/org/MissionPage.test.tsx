import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { MissionPage } from './MissionPage';

const MISSION = {
  id: 'm1',
  title: 'Ship v2 launch',
  goal_text: 'Coordinate the v2 launch across teams',
  status: 'active',
  agent_count: 3,
  risk_level: 'high',
  spent_usd: 1.5,
  budget_usd: 10,
};

const TASKS = {
  data: [
    { id: 't1', title: 'Write release notes', status: 'completed', mission_id: 'm1' },
    { id: 't2', title: 'Cut the branch', status: 'queued', mission_id: 'm1' },
  ],
};

function mockFetch(mission: unknown = MISSION) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/missions/m1'))
      return new Response(JSON.stringify(mission), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/tasks'))
      return new Response(JSON.stringify(TASKS), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><MissionPage orgId="o1" missionId="m1" /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('MissionPage', () => {
  test('renders the mission header, goal, status and metrics', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByRole('heading', { name: /Ship v2 launch/i })).toBeInTheDocument();
    expect(screen.getByText('Coordinate the v2 launch across teams')).toBeInTheDocument();
    expect(screen.getByText('Active')).toBeInTheDocument();
    expect(screen.getByText('HIGH')).toBeInTheDocument();
    expect(screen.getByText('3 agents')).toBeInTheDocument();
  });

  test('computes task progress from the tasks payload', async () => {
    mockFetch();
    renderPage();
    // 1 of 2 tasks completed → "1/2 tasks" and 50% progress.
    expect(await screen.findByText('1/2 tasks')).toBeInTheDocument();
    expect(screen.getByText('50%')).toBeInTheDocument();
    expect(screen.getByText('Tasks (2)')).toBeInTheDocument();
  });

  test('renders the kanban task cards for the default Tasks tab', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByText('Write release notes')).toBeInTheDocument();
    expect(screen.getByText('Cut the branch')).toBeInTheDocument();
  });

  test('renders a not-found state when the mission is missing', async () => {
    mockFetch(null);
    renderPage();
    expect(await screen.findByText('Mission not found.')).toBeInTheDocument();
  });
});
