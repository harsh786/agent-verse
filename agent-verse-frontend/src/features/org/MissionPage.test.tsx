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

function mockFetch(mission: unknown = MISSION, tasks: unknown = TASKS) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/missions/m1'))
      return new Response(JSON.stringify(mission), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/tasks'))
      return new Response(JSON.stringify(tasks), { status: 200, headers: { 'Content-Type': 'application/json' } });
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

  test('falls back to defaults for a mission with minimal fields', async () => {
    mockFetch({ id: 'm1', status: 'draft' });
    renderPage();
    expect(await screen.findByRole('heading', { name: 'Untitled Mission' })).toBeInTheDocument();
    expect(screen.getByText('Draft')).toBeInTheDocument();
    // No risk_level → defaults to "medium" → "MEDIUM" badge (a kanban task
    // card may also show a "medium" priority badge, so allow multiple).
    expect(screen.getAllByText('MEDIUM').length).toBeGreaterThan(0);
    // No agent_count → "0 agents"; no spent_usd → "$0.00"; no budget_usd → no "/ $X" suffix.
    expect(screen.getByText('0 agents')).toBeInTheDocument();
    expect(screen.getByText('$0.00')).toBeInTheDocument();
  });

  test('shows 0/0 tasks and 0% progress when the mission has no tasks yet', async () => {
    mockFetch(MISSION, { data: [] });
    renderPage();
    expect(await screen.findByText('0/0 tasks')).toBeInTheDocument();
    expect(screen.getByText('Tasks (0)')).toBeInTheDocument();
    expect(screen.getByText('0%')).toBeInTheDocument();
  });

  test('falls back to the draft status config for an unrecognized status', async () => {
    mockFetch({ id: 'm1', status: 'some-unknown-status' as never });
    renderPage();
    expect(await screen.findByRole('heading', { name: 'Untitled Mission' })).toBeInTheDocument();
    expect(screen.getByText('Draft')).toBeInTheDocument();
  });

  test('auto-opens the Output tab and renders the deliverable when the mission has outputs', async () => {
    mockFetch({
      ...MISSION,
      outputs: ['Launch summary drafted'],
      evidence: [],
    });
    renderPage();
    expect(await screen.findByText(/Output/i)).toBeInTheDocument();
    expect(await screen.findByText(/launch summary drafted/i)).toBeInTheDocument();
  });

  test('auto-opens the Output tab for a publish receipt even with no outputs', async () => {
    mockFetch({
      ...MISSION,
      published: {
        server_id: 'slack', tool_name: 'post_message', success: true,
        output: {}, published_at: '2026-09-11T10:07:47Z',
      },
    });
    renderPage();
    expect(await screen.findByText(/published/i)).toBeInTheDocument();
  });

  test('auto-opens the Output tab when a publish is pending approval', async () => {
    mockFetch({ ...MISSION, publish_pending: true });
    renderPage();
    expect(await screen.findByText(/awaiting your approval/i)).toBeInTheDocument();
  });

  test('the back button navigates back', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /Ship v2 launch/i });
    // Just verify it's clickable without throwing (navigate(-1) inside a MemoryRouter is a no-op).
    await import('@testing-library/user-event').then(({ default: userEvent }) =>
      userEvent.click(screen.getByRole('button', { name: /Go back/i })),
    );
    expect(screen.getByRole('heading', { name: /Ship v2 launch/i })).toBeInTheDocument();
  });

  test('switching to the Artifacts and Activity tabs renders their panels', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('Write release notes');
    const userEvent = (await import('@testing-library/user-event')).default;
    await userEvent.click(screen.getByRole('button', { name: 'Artifacts' }));
    // Tasks-tab content (the kanban card) is gone once we switch away.
    expect(screen.queryByText('Write release notes')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Activity' }));
    expect(screen.getByRole('button', { name: 'Activity' })).toHaveAttribute('aria-selected', 'true');
  });
});
