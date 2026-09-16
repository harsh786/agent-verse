import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { TeamPage } from './TeamPage';

const MEMBERS = {
  team_id: 't1',
  member_ids: ['a1', 'a2'],
  members: [
    { id: 'a1', name: 'Ada Agent', role: 'Engineer', status: 'executing', current_task: 'Writing tests' },
    { id: 'a2', name: 'Grace Agent', role: 'Reviewer', status: 'idle' },
  ],
};

function mockFetch(members: unknown = MEMBERS) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/members'))
      return new Response(JSON.stringify(members), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/tasks'))
      return new Response(JSON.stringify({ data: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/events'))
      return new Response(JSON.stringify({ data: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/orgs/o1/teams/t1']}>
        <Routes>
          <Route path="/orgs/:orgId/teams/:teamId" element={<TeamPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('TeamPage', () => {
  test('renders team members with their role and current task', async () => {
    mockFetch();
    renderPage();
    expect(screen.getByRole('heading', { name: 'Mission Team' })).toBeInTheDocument();
    expect(await screen.findByText('Ada Agent')).toBeInTheDocument();
    expect(screen.getByText('Grace Agent')).toBeInTheDocument();
    expect(screen.getByText('Engineer')).toBeInTheDocument();
    expect(screen.getByText('"Writing tests"')).toBeInTheDocument();
  });

  test('switching to the Task Board tab mounts the kanban columns', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('Ada Agent');
    await userEvent.click(screen.getByRole('tab', { name: /Task Board/i }));
    expect(await screen.findByText('TODO')).toBeInTheDocument();
    expect(screen.getByText('IN PROGRESS')).toBeInTheDocument();
  });

  test('renders an empty state when there are no team members', async () => {
    mockFetch({ team_id: 't1', member_ids: [], members: [] });
    renderPage();
    expect(await screen.findByText('No team members available yet.')).toBeInTheDocument();
  });
});
