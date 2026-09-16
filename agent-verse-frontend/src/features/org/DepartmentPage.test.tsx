import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { DepartmentPage } from './DepartmentPage';

const DEPARTMENTS = [
  {
    id: 'd1',
    name: 'Engineering',
    purpose: 'Ship the product',
    capability_domains: ['backend', 'frontend'],
    agent_count: 7,
  },
];

const MISSIONS = {
  data: [
    { id: 'm1', title: 'Launch API', status: 'active', dept_id: 'd1' },
    { id: 'm2', title: 'Rebrand site', status: 'completed', dept_id: 'd2' },
  ],
  cursor: null,
  hasMore: false,
};

function mockFetch(departments: unknown = DEPARTMENTS) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/departments'))
      return new Response(JSON.stringify(departments), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/missions'))
      return new Response(JSON.stringify(MISSIONS), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/tasks'))
      return new Response(JSON.stringify({ data: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPage(deptId = 'd1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/orgs/o1/departments/${deptId}`]}>
        <Routes>
          <Route path="/orgs/:orgId/departments/:deptId" element={<DepartmentPage />} />
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

describe('DepartmentPage', () => {
  test('renders the department name, purpose and capabilities', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByRole('heading', { name: /Engineering/i })).toBeInTheDocument();
    expect(screen.getAllByText('Ship the product').length).toBeGreaterThan(0);
    expect(screen.getByText('backend')).toBeInTheDocument();
    expect(screen.getByText('frontend')).toBeInTheDocument();
  });

  test('shows only this department\'s missions and its agent count', async () => {
    mockFetch();
    renderPage();
    // m1 belongs to d1 and is surfaced; m2 belongs to d2 and is filtered out.
    expect(await screen.findByText('Launch API')).toBeInTheDocument();
    expect(screen.queryByText('Rebrand site')).not.toBeInTheDocument();
    // Agent count stat comes straight from the department payload.
    expect(screen.getByText('7')).toBeInTheDocument();
  });

  test('switching to the Tasks tab renders the empty task state', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('Launch API');
    await userEvent.click(screen.getByRole('tab', { name: /Tasks/i }));
    expect(await screen.findByText('No tasks for this department.')).toBeInTheDocument();
  });

  test('renders a not-found state when the department id is unknown', async () => {
    mockFetch();
    renderPage('does-not-exist');
    expect(await screen.findByText('Department not found.')).toBeInTheDocument();
  });
});
