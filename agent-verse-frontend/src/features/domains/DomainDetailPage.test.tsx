import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
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

  test('one-click deploy shows an error toast when the API returns no agent_id', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/deploy'))
        return new Response(JSON.stringify({ success: false, error: 'quota exceeded' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/marketplace/templates'))
        return new Response(JSON.stringify({ templates: AGENT_TEMPLATES, total: 1, page: 1, page_size: 50 }),
          { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response(JSON.stringify(GOAL_TEMPLATES), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderPage();
    await screen.findByText('Code Review Bot');
    await userEvent.click(screen.getByRole('button', { name: /deploy code review bot/i }));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    // Deployed state must never appear; button reverts to the deploy label.
    expect(screen.queryByText(/Deployed/i)).not.toBeInTheDocument();
    expect(await screen.findByRole('button', { name: /deploy code review bot/i })).toBeInTheDocument();
  });

  test('one-click deploy shows an error toast when the request throws', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/deploy')) throw new Error('network down');
      if (url.includes('/marketplace/templates'))
        return new Response(JSON.stringify({ templates: AGENT_TEMPLATES, total: 1, page: 1, page_size: 50 }),
          { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response(JSON.stringify(GOAL_TEMPLATES), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderPage();
    await screen.findByText('Code Review Bot');
    await userEvent.click(screen.getByRole('button', { name: /deploy code review bot/i }));
    expect(await screen.findByRole('button', { name: /deploy code review bot/i })).toBeInTheDocument();
    expect(screen.queryByText(/Deployed/i)).not.toBeInTheDocument();
  });

  test('template with required parameters shows a Configure & Deploy button and opens the modal', async () => {
    const templateWithParams = [
      {
        ...AGENT_TEMPLATES[0],
        template_id: 'tpl-2',
        name: 'Config Bot',
        author_name: 'Jane Dev',
        required_connectors: ['github', 'slack', 'jira', 'notion', 'linear'],
        parameters_schema: {
          properties: {
            repo: { type: 'string', description: 'Repository name' },
            env: { type: 'string', enum: ['staging', 'prod'], default: 'staging' },
          },
          required: ['repo'],
        },
      },
    ];
    mockFetch({ agents: templateWithParams, goals: [] });
    renderPage();
    expect(await screen.findByText('Config Bot')).toBeInTheDocument();
    // author_name rendered
    expect(screen.getByText(/by Jane Dev/)).toBeInTheDocument();
    // more-than-4 connectors collapses into a "+N more" pill
    expect(screen.getByText('+1 more')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /configure and deploy config bot/i }));

    // Modal open: required field warning shown since "repo" isn't filled
    expect(screen.getByText(/Fill in required fields/i)).toBeInTheDocument();
    const deployBtn = screen.getByRole('button', { name: /^Deploy Agent$/i });
    expect(deployBtn).toBeDisabled();

    // Fill required text input
    const repoInput = screen.getByLabelText(/repo/i, { selector: 'input' });
    await userEvent.type(repoInput, 'my-org/my-repo');

    // Exercise the enum select branch
    const envSelect = screen.getByLabelText(/env/i, { selector: 'select' });
    await userEvent.selectOptions(envSelect, 'prod');

    expect(deployBtn).toBeEnabled();
    await userEvent.click(deployBtn);

    expect(await screen.findByText(/Agent deployed!/i)).toBeInTheDocument();
    expect(screen.getByText('agent-xyz')).toBeInTheDocument();

    // Closing via the success-state "Close" link works. Both the modal's X
    // icon (aria-label="Close") and this text link resolve to the same
    // accessible name, so scope by the link's distinct styling.
    const successCloseLink = screen.getByText('Close', { selector: 'button.text-green-700' });
    await userEvent.click(successCloseLink);
    expect(screen.queryByText(/Agent deployed!/i)).not.toBeInTheDocument();
  });

  test('modal deploy shows an error toast when the API returns no agent_id', async () => {
    const templateWithParams = [
      {
        ...AGENT_TEMPLATES[0],
        template_id: 'tpl-3',
        name: 'Failing Bot',
        // DomainAgentCard only shows the "Configure & Deploy" button (which
        // opens this modal) when required.length > 0; the modal itself shows
        // the "no required parameters" copy when properties is empty. Both
        // must hold simultaneously to exercise this path via the real UI.
        parameters_schema: { properties: {}, required: ['ghost_param'] },
      },
    ];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/deploy'))
        return new Response(JSON.stringify({ success: false, error: 'boom' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/marketplace/templates'))
        return new Response(JSON.stringify({ templates: templateWithParams, total: 1, page: 1, page_size: 50 }),
          { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderPage();
    await screen.findByText('Failing Bot');
    // No required params at all -> falls back to "no required parameters" copy,
    // but template_config still routes through Configure (has parameters_schema present).
    await userEvent.click(screen.getByRole('button', { name: /configure and deploy failing bot/i }));
    expect(screen.getByText(/This agent has no required parameters/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /^Deploy Agent$/i }));
    // No <Toaster/> is mounted in this render tree, so the error toast never
    // reaches the DOM — assert via the Zustand toast store instead.
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'error' && /boom/i.test(t.message))).toBe(true);
    });
  });

  test('modal deploy shows an error toast when the request throws', async () => {
    const templateWithParams = [
      {
        ...AGENT_TEMPLATES[0],
        template_id: 'tpl-4',
        name: 'Throwing Bot',
        parameters_schema: { properties: {}, required: ['ghost_param'] },
      },
    ];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/deploy')) throw new Error('kaboom');
      if (url.includes('/marketplace/templates'))
        return new Response(JSON.stringify({ templates: templateWithParams, total: 1, page: 1, page_size: 50 }),
          { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderPage();
    await screen.findByText('Throwing Bot');
    await userEvent.click(screen.getByRole('button', { name: /configure and deploy throwing bot/i }));
    await userEvent.click(screen.getByRole('button', { name: /^Deploy Agent$/i }));
    // Modal stays open (deploy failed), no success panel rendered.
    await waitFor(() => expect(screen.queryByText(/Agent deployed!/i)).not.toBeInTheDocument());
  });

  test('closing the configure modal via the backdrop and the X button both work', async () => {
    const templateWithParams = [
      { ...AGENT_TEMPLATES[0], template_id: 'tpl-5', name: 'Closable Bot', parameters_schema: { properties: {}, required: ['x'] } },
    ];
    mockFetch({ agents: templateWithParams, goals: [] });
    renderPage();
    await screen.findByText('Closable Bot');

    await userEvent.click(screen.getByRole('button', { name: /configure and deploy closable bot/i }));
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('button', { name: '^Deploy Agent$' })).not.toBeInTheDocument();

    // Re-open, then close via Cancel button
    await userEvent.click(screen.getByRole('button', { name: /configure and deploy closable bot/i }));
    await userEvent.click(screen.getByRole('button', { name: /^Cancel$/i }));
    expect(screen.queryByText(/Fill in required fields/i)).not.toBeInTheDocument();
  });

  test('goal template with parameters opens the instantiator and "Use in Goal" navigates', async () => {
    const goalWithParams = [
      {
        id: 'goal-2', name: 'Parametrized goal', description: 'desc',
        goal_text: 'Do {{thing}}', domain: 'software',
        parameters: [{ name: 'thing', description: 'the thing', required: true }],
        use_count: 1, version: 1, created_at: '2026-01-01T00:00:00Z',
      },
    ];
    mockFetch({ agents: [], goals: goalWithParams });
    renderPage();
    expect(await screen.findByText('Parametrized goal')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /use template: parametrized goal/i }));

    // TemplateInstantiator is now open
    expect(screen.queryByText(/This template has no parameters/i)).not.toBeInTheDocument();
    const thingInput = screen.getByLabelText(/thing/i);
    await userEvent.type(thingInput, 'the-task');
    await userEvent.click(screen.getByRole('button', { name: /use in goal/i }));

    // Instantiator closes itself after handing off
    await waitFor(() => expect(screen.queryByText(/Preview/i)).not.toBeInTheDocument());
  });

  test('goal template without parameters navigates straight to /goals with the prefill', async () => {
    const goalNoParams = [
      {
        id: 'goal-3', name: 'Simple goal', description: 'desc',
        goal_text: 'Just do it', domain: 'software', parameters: [],
        use_count: 0, version: 1, created_at: '2026-01-01T00:00:00Z',
      },
    ];
    mockFetch({ agents: [], goals: goalNoParams });
    renderPage();
    expect(await screen.findByText('Simple goal')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /use template: simple goal/i }));
    // No instantiator modal appears since navigation happens directly.
    expect(screen.queryByText(/Preview/i)).not.toBeInTheDocument();
  });

  test('renders only the Agent Templates column when there are no goal templates', async () => {
    mockFetch({ agents: AGENT_TEMPLATES, goals: [] });
    renderPage();
    expect(await screen.findByRole('heading', { name: 'Agent Templates' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Goal Templates' })).not.toBeInTheDocument();
  });

  test('renders only the Goal Templates column when there are no agent templates', async () => {
    mockFetch({ agents: [], goals: GOAL_TEMPLATES });
    renderPage();
    expect(await screen.findByRole('heading', { name: 'Goal Templates' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Agent Templates' })).not.toBeInTheDocument();
  });

  test('empty-state "Create Template" button navigates to /templates', async () => {
    mockFetch({ agents: [], goals: [] });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: /Create Template/i }));
    // Navigating unmounts this page's empty state (route has no matching path for /templates here).
    await waitFor(() => expect(screen.queryByText(/No templates for/i)).not.toBeInTheDocument());
  });
});
