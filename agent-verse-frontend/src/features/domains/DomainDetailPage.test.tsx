import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import DomainDetailPage from './DomainDetailPage';

const AGENT_TEMPLATES = [
  {
    template_id: 'tpl-1', slug: 'code-review', name: 'Code Review Bot',
    description: 'Reviews pull requests automatically', domain: 'software',
    required_connectors: ['github'], autonomy_mode: 'supervised', visibility: 'public',
    review_status: 'approved', is_builtin: true, is_verified: true, install_count: 1234, version: '1.0',
  },
];

const GOAL_TEMPLATES = [
  {
    id: 'goal-1', name: 'Ship a hotfix', description: 'Deploy a hotfix to prod',
    goal_text: 'Deploy hotfix {{ticket}}', domain: 'software', parameters: [],
    use_count: 5, version: 1, created_at: '2026-01-01T00:00:00Z',
  },
];

function mockFetch(opts: { agents?: unknown[]; goals?: unknown[] } = {}) {
  const agents = opts.agents ?? AGENT_TEMPLATES;
  const goals = opts.goals ?? GOAL_TEMPLATES;
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/marketplace/templates/') && url.includes('/deploy') && method === 'POST')
      return new Response(JSON.stringify({ success: true, agent_id: 'agent-xyz', agent_name: 'Code Review Bot' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/marketplace/templates'))
      return new Response(JSON.stringify({ templates: agents, total: agents.length, page: 1, page_size: 50 }),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/templates'))
      return new Response(JSON.stringify(goals), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPage(domain = 'software') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/domains/${domain}`]}>
        <Routes>
          <Route path="/domains/:domain" element={<DomainDetailPage />} />
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

describe('DomainDetailPage', () => {
  test('renders the domain hero from the route param metadata', async () => {
    mockFetch();
    renderPage('software');
    expect(await screen.findByRole('heading', { name: 'Software Engineering' })).toBeInTheDocument();
    expect(screen.getByText(/AI pair programmer for your whole team/i)).toBeInTheDocument();
  });

  test('renders both agent and goal templates for the domain', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByText('Code Review Bot')).toBeInTheDocument();
    expect(screen.getByText('Ship a hotfix')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Agent Templates' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Goal Templates' })).toBeInTheDocument();
  });

  test('stats strip reflects the template counts', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('Code Review Bot');
    expect(screen.getByText('agent template')).toBeInTheDocument();
    expect(screen.getByText('goal template')).toBeInTheDocument();
    // The bold count values.
    const counts = screen.getAllByText('1', { selector: 'strong' });
    expect(counts.length).toBeGreaterThanOrEqual(2);
  });

  test('scopes both requests to the domain key', async () => {
    const spy = mockFetch();
    renderPage('devops');
    await screen.findByRole('heading', { name: 'DevOps & SRE' });
    await waitFor(() =>
      expect(spy.mock.calls.some(([u]) => String(u).includes('/marketplace/templates') && String(u).includes('domain=devops'))).toBe(true),
    );
    expect(spy.mock.calls.some(([u]) => /\/templates\?.*domain=devops/.test(String(u)))).toBe(true);
  });

  test('one-click deploy POSTs to the deploy endpoint and shows the deployed state', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('Code Review Bot');
    await userEvent.click(screen.getByRole('button', { name: /deploy code review bot/i }));

    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        /\/marketplace\/templates\/tpl-1\/deploy$/.test(String(u)) && (i as RequestInit)?.method === 'POST',
      )).toBe(true),
    );
    expect(await screen.findByText(/Deployed/i)).toBeInTheDocument();
  });

  test('renders the empty state when the domain has no templates', async () => {
    mockFetch({ agents: [], goals: [] });
    renderPage();
    expect(await screen.findByText(/No templates for Software Engineering yet/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Create Template/i })).toBeInTheDocument();
  });

  test('unknown domain key falls back to the raw key as the title', async () => {
    mockFetch({ agents: [], goals: [] });
    renderPage('made-up-domain');
    expect(await screen.findByRole('heading', { name: 'made-up-domain' })).toBeInTheDocument();
  });
});
