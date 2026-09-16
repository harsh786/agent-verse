import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { CollaborationPage } from './CollaborationPage';

// ─── Mock the websocket hook with a controllable, module-scoped state ────────
// `vi.hoisted` gives us a stable object the mock factory can close over, so
// individual tests can flip `autoOpen` off (offline path) or inspect calls to
// `sendMessage` / capture the `onMessage` callback the component registered.
const hoisted = vi.hoisted(() => ({
  autoOpen: true,
  onMessage: undefined as ((data: unknown) => void) | undefined,
  sendMessage: vi.fn(),
}));

vi.mock('@/lib/ws/useCollabSocket', () => ({
  useCollabSocket: (opts: {
    onMessage: (data: unknown) => void;
    onOpen?: () => void;
    onClose?: () => void;
  }) => {
    hoisted.onMessage = opts.onMessage;
    if (hoisted.autoOpen) {
      setTimeout(() => opts.onOpen?.(), 0);
    }
    return { sendMessage: hoisted.sendMessage };
  },
}));

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

interface RouteMatcher {
  suffix: string;
  method?: string;
  handler: () => Response | Promise<Response>;
}

function buildFetchMock(routes: RouteMatcher[], fallback: () => Response = () => jsonResponse([])) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    const match = routes.find((r) => url.endsWith(r.suffix) && (r.method ?? 'GET') === method);
    return match ? match.handler() : fallback();
  });
}

function sessionRoutes(
  session: Record<string, unknown>,
  opts: {
    operations?: unknown[];
    consensus?: unknown;
    extra?: RouteMatcher[];
  } = {}
): RouteMatcher[] {
  const id = session.session_id as string;
  return [
    { suffix: '/collab/sessions', method: 'GET', handler: () => jsonResponse([session]) },
    { suffix: `/collab/sessions/${id}/operations`, method: 'GET', handler: () => jsonResponse(opts.operations ?? []) },
    {
      suffix: `/collab/sessions/${id}/consensus`,
      method: 'GET',
      handler: () => jsonResponse(opts.consensus ?? { agreed: false, summary: '' }),
    },
    ...(opts.extra ?? []),
  ];
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <CollaborationPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

async function openPanelFor(session: Record<string, unknown>) {
  renderPage();
  const card = await screen.findByText(session.name as string);
  await userEvent.click(card);
  await screen.findByTestId('live-session');
}

const DEBATE_SESSION = {
  session_id: 'sess-debate-1',
  name: 'Architecture Debate',
  mode: 'debate',
  participants: ['human:lead', 'agent:critic'],
  participant_count: 2,
  status: 'active',
  content: '',
  goal_id: null,
  agent_id: null,
  created_at: '2026-06-25T00:00:00+00:00',
};

const REVIEW_SESSION = {
  session_id: 'sess-review-1',
  name: 'Doc Review',
  mode: 'review',
  participants: ['human:lead', 'agent:critic'],
  participant_count: 2,
  status: 'active',
  content: 'Initial draft content',
  goal_id: null,
  agent_id: null,
  created_at: '2026-06-25T00:00:00+00:00',
};

beforeEach(() => {
  useAuthStore.setState({ apiKey: 'key-a', tenantId: 'tid-a', plan: 'free', isAuthenticated: true });
  hoisted.autoOpen = true;
  hoisted.onMessage = undefined;
  hoisted.sendMessage.mockClear();
  localStorage.clear();
});
afterEach(() => vi.restoreAllMocks());

// ─── CollaborationPage (list / create view) ───────────────────────────────────

describe('CollaborationPage list view', () => {
  test('shows loading state before sessions resolve', () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise(() => {}));
    renderPage();
    expect(screen.getByText(/Loading sessions/)).toBeInTheDocument();
  });

  test('shows error state when sessions fail to load', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('network down'));
    renderPage();
    expect(await screen.findByText(/Failed to load sessions/)).toBeInTheDocument();
  });

  test('shows empty state when there are no sessions', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse([]));
    renderPage();
    expect(await screen.findByText(/No sessions yet/)).toBeInTheDocument();
  });

  test('filters sessions by status and updates the pluralized count', async () => {
    const active = { ...DEBATE_SESSION, session_id: 's-active', name: 'Active One', status: 'active' };
    const closed = { ...DEBATE_SESSION, session_id: 's-closed', name: 'Closed One', status: 'closed' };
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse([active, closed]));
    renderPage();

    await screen.findByText('Active One');
    expect(screen.getByText('2 sessions')).toBeInTheDocument();

    await userEvent.click(screen.getByText('Active'));
    expect(screen.getByText('1 session')).toBeInTheDocument();
    expect(screen.getByText('Active One')).toBeInTheDocument();
    expect(screen.queryByText('Closed One')).not.toBeInTheDocument();

    await userEvent.click(screen.getByText('Closed'));
    expect(screen.getByText('1 session')).toBeInTheDocument();
    expect(screen.getByText('Closed One')).toBeInTheDocument();
    expect(screen.queryByText('Active One')).not.toBeInTheDocument();

    await userEvent.click(screen.getByText('All'));
    expect(screen.getByText('2 sessions')).toBeInTheDocument();
  });

  test('renders session card with goal/agent ids and a >4 participants overflow badge, and no participants row for zero participants', async () => {
    const many = {
      ...DEBATE_SESSION,
      session_id: 's-many',
      name: 'Many Participants',
      goal_id: 'goal-77',
      agent_id: 'agent-77',
      participants: ['human:a', 'human:b', 'human:c', 'agent:d', 'agent:e', 'agent:f'],
      participant_count: 6,
    };
    const zero = {
      ...DEBATE_SESSION,
      session_id: 's-zero',
      name: 'Zero Participants',
      participants: [],
      participant_count: 0,
      created_at: undefined,
    };
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse([many, zero]));
    renderPage();

    await screen.findByText('Many Participants');
    expect(screen.getByText('goal-77')).toBeInTheDocument();
    expect(screen.getByText('agent-77')).toBeInTheDocument();
    expect(screen.getByText('+2')).toBeInTheDocument();

    const zeroCard = screen.getByText('Zero Participants').closest('[data-testid="session-card"]');
    expect(zeroCard).toBeTruthy();
    expect(zeroCard!.querySelectorAll('[title]').length).toBe(0);
  });

  test('quick templates prefill the name and mode', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse([]));
    renderPage();
    await userEvent.click(await screen.findByText(/\+ New Session/));

    const cases: Array<[string, string]> = [
      ['Code Review', 'Review'],
      ['Product Planning', 'Brainstorm'],
      ['Architecture Decision', 'Debate'],
      ['Bug Triage', 'Suggest'],
    ];
    // "Session mode" section scope: each mode label (e.g. "Brainstorm") also
    // appears in the "Quick templates" card badges above it, so an unscoped
    // getByText matches two elements.
    const modeSection = screen.getByText('Session mode').parentElement!;

    for (const [tplName, modeLabel] of cases) {
      await userEvent.click(screen.getByText(tplName));
      expect(screen.getByPlaceholderText('Session name')).toHaveValue(tplName);
      const modeButton = within(modeSection).getByText(modeLabel).closest('button');
      expect(modeButton?.className).toContain('border-primary');
    }
  });

  test('session mode selector buttons toggle the selected style', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse([]));
    renderPage();
    await userEvent.click(await screen.findByText(/\+ New Session/));

    const modeSection = screen.getByText('Session mode').parentElement!;
    for (const label of ['Review', 'Suggest', 'Debate', 'Brainstorm']) {
      const button = within(modeSection).getByText(label).closest('button')!;
      await userEvent.click(button);
      expect(button.className).toContain('border-primary');
    }
  });

  test('shows create error message when session creation fails', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch');
    fetchMock.mockImplementation(
      buildFetchMock([
        { suffix: '/collab/sessions', method: 'GET', handler: () => jsonResponse([]) },
        {
          suffix: '/collab/sessions',
          method: 'POST',
          handler: () => jsonResponse({ error: { message: 'Name is required' } }, 400),
        },
      ])
    );
    renderPage();
    await userEvent.click(await screen.findByText(/\+ New Session/));
    await userEvent.click(screen.getByText('Create Session'));
    expect(await screen.findByText(/Name is required/)).toBeInTheDocument();
  });

  test('cancel button toggles the create panel label', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse([]));
    renderPage();
    const toggle = await screen.findByTestId('create-session-btn');
    expect(toggle).toHaveTextContent('+ New Session');
    await userEvent.click(toggle);
    expect(toggle).toHaveTextContent('✕ Cancel');
    await userEvent.click(toggle);
    expect(toggle).toHaveTextContent('+ New Session');
  });

  test('clicking an existing session card opens the live session panel', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(buildFetchMock(sessionRoutes(DEBATE_SESSION)));
    renderPage();
    await userEvent.click(await screen.findByText(DEBATE_SESSION.name));
    expect(await screen.findByTestId('live-session')).toBeInTheDocument();
  });
});

// ─── LiveSessionPanel ──────────────────────────────────────────────────────────

describe('LiveSessionPanel', () => {
  test('shows the Rounds section with vote counts and submits a round via button and Enter', async () => {
    const ops = [
      { operation_id: 'op-1', version: 1, author: 'human:lead', operation: { round_type: 'agree', content: 'On board' }, created_at: new Date(Date.now() - 120_000).toISOString() },
      { operation_id: 'op-2', version: 2, author: 'agent:critic', operation: { round_type: 'agree', content: 'Also agree' }, created_at: new Date(Date.now() - 7_200_000).toISOString() },
      { operation_id: 'op-3', version: 3, author: 'human:lead', operation: { round_type: 'disagree', content: 'Not sure' }, created_at: new Date(Date.now() - 30_000).toISOString() },
    ];
    let roundCalls = 0;
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      buildFetchMock(
        sessionRoutes(DEBATE_SESSION, {
          operations: ops,
          extra: [
            {
              suffix: `/collab/sessions/${DEBATE_SESSION.session_id}/rounds`,
              method: 'POST',
              handler: () => {
                roundCalls += 1;
                return jsonResponse({ ok: true });
              },
            },
          ],
        })
      )
    );

    await openPanelFor(DEBATE_SESSION);
    const roundsSection = screen.getByTestId('rounds-section');
    expect(within(roundsSection).getByText('Rounds')).toBeInTheDocument();
    expect(within(roundsSection).getByText('2')).toBeInTheDocument();
    expect(within(roundsSection).getByText('1')).toBeInTheDocument();

    await userEvent.click(within(roundsSection).getByText('Critique'));
    const roundInput = within(roundsSection).getByPlaceholderText(/something…/);
    await userEvent.type(roundInput, 'A critique point');
    await userEvent.click(within(roundsSection).getByText('Submit'));
    await waitFor(() => expect(roundCalls).toBe(1));

    await userEvent.type(roundInput, 'Another point{Enter}');
    await waitFor(() => expect(roundCalls).toBe(2));
  });

  test('Facilitate AI is disabled while offline and shows the Offline indicator', async () => {
    hoisted.autoOpen = false;
    vi.spyOn(globalThis, 'fetch').mockImplementation(buildFetchMock(sessionRoutes(DEBATE_SESSION)));
    await openPanelFor(DEBATE_SESSION);

    expect(screen.getByText('Offline')).toBeInTheDocument();
    expect(screen.getByText(/AI Facilitate/).closest('button')).toBeDisabled();
  });

  test('Facilitate AI is enabled while online and appends a system message', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(buildFetchMock(sessionRoutes(DEBATE_SESSION)));
    await openPanelFor(DEBATE_SESSION);

    await screen.findByText('Live');
    const facilitateButton = screen.getByText(/AI Facilitate/).closest('button')!;
    expect(facilitateButton).not.toBeDisabled();
    await userEvent.click(facilitateButton);
    expect(await screen.findByText(/Analyzing discussion state/)).toBeInTheDocument();
    expect(hoisted.sendMessage).toHaveBeenCalledWith(expect.objectContaining({ type: 'facilitate' }));
  });

  test('sends a live chat message via Enter and ignores blank input', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(buildFetchMock(sessionRoutes(DEBATE_SESSION)));
    await openPanelFor(DEBATE_SESSION);
    await screen.findByText('Live');

    const input = screen.getByPlaceholderText('Send a message…');
    const sendButton = screen.getByPlaceholderText('Send a message…').closest('div')!.querySelector('button')!;
    expect(sendButton).toBeDisabled();

    await userEvent.type(input, '   {Enter}');
    expect(hoisted.sendMessage).not.toHaveBeenCalled();

    await userEvent.clear(input);
    await userEvent.type(input, 'hello team{Enter}');
    expect(hoisted.sendMessage).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'message', content: 'hello team' })
    );
    expect(await screen.findByText('hello team')).toBeInTheDocument();
  });

  test('generates insights, reflects agreement-level colors, and persists action-item toggles', async () => {
    const insightResponses = [
      {
        key_decisions: ['Use Postgres'],
        action_items: ['Write migration', 'Update docs'],
        open_questions: ['Who owns rollout?'],
        agreement_level: 0.85,
        sentiment: 'positive',
        summary: 'Team is aligned.',
      },
      {
        key_decisions: [],
        action_items: [],
        open_questions: [],
        agreement_level: 0.5,
        sentiment: 'neutral',
        summary: 'Mixed feelings.',
      },
      {
        key_decisions: [],
        action_items: [],
        open_questions: [],
        agreement_level: 0.1,
        sentiment: 'negative',
        summary: 'Disagreement.',
      },
    ];
    let call = 0;
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      buildFetchMock(
        sessionRoutes(DEBATE_SESSION, {
          extra: [
            {
              suffix: `/collab/sessions/${DEBATE_SESSION.session_id}/insights`,
              method: 'POST',
              handler: () => jsonResponse(insightResponses[Math.min(call++, insightResponses.length - 1)]),
            },
          ],
        })
      )
    );

    await openPanelFor(DEBATE_SESSION);
    const generate = () => screen.getByTestId('generate-insights-btn');

    await userEvent.click(generate());
    expect(await screen.findByText('Use Postgres')).toBeInTheDocument();
    expect(screen.getByText('Who owns rollout?')).toBeInTheDocument();
    expect(screen.getByText('85% agreement').className).toContain('bg-green-100');

    const item = screen.getByText('Write migration');
    const checkbox = item.parentElement!.querySelector('input[type="checkbox"]')!;
    fireEvent.click(checkbox);
    expect(screen.getByText('Write migration').className).toContain('line-through');
    expect(localStorage.getItem(`av_collab_actions_${DEBATE_SESSION.session_id}`)).toBe(
      JSON.stringify(['Write migration'])
    );

    await userEvent.click(generate());
    await waitFor(() => expect(screen.getByText('50% agreement').className).toContain('bg-amber-100'));

    await userEvent.click(generate());
    await waitFor(() => expect(screen.getByText('10% agreement').className).toContain('bg-red-100'));
  });

  test('delegation panel enables submit only when all fields are filled and shows success', async () => {
    let delegateCalls = 0;
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      buildFetchMock(
        sessionRoutes(DEBATE_SESSION, {
          extra: [
            {
              suffix: `/collab/sessions/${DEBATE_SESSION.session_id}/delegate`,
              method: 'POST',
              handler: () => {
                delegateCalls += 1;
                return jsonResponse({ ok: true });
              },
            },
          ],
        })
      )
    );
    await openPanelFor(DEBATE_SESSION);

    // "Delegate Task" appears twice (the panel heading and the submit button
    // itself), so scope the query to the delegate-panel container.
    const submit = screen.getByTestId('delegate-panel').querySelector(
      'button[class*="bg-sky-500"]'
    ) as HTMLButtonElement;
    expect(submit).toBeDisabled();

    await userEvent.type(screen.getByPlaceholderText('From agent ID'), 'agent:a');
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByPlaceholderText('To agent ID'), 'agent:b');
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByPlaceholderText('Sub-task description'), 'Investigate bug');
    expect(submit).not.toBeDisabled();

    await userEvent.click(submit);
    expect(await screen.findByText(/Delegated successfully/)).toBeInTheDocument();
    await waitFor(() => expect(delegateCalls).toBe(1));
    expect(screen.getByPlaceholderText('From agent ID')).toHaveValue('');
    expect(screen.getByPlaceholderText('To agent ID')).toHaveValue('');
    expect(screen.getByPlaceholderText('Sub-task description')).toHaveValue('');
  });

  test('consensus card shows agreed state with dissenter, and a disagreed state', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      buildFetchMock(
        sessionRoutes(DEBATE_SESSION, {
          consensus: { agreed: true, summary: 'Mostly aligned', dissenter: 'agent:critic' },
        })
      )
    );
    await openPanelFor(DEBATE_SESSION);
    expect(await screen.findByText('Consensus reached')).toBeInTheDocument();
    expect(screen.getByText('Mostly aligned')).toBeInTheDocument();
    expect(screen.getByText(/Dissenter: agent:critic/)).toBeInTheDocument();

    let refetchCount = 0;
    const consensusRoute: RouteMatcher = {
      suffix: `/collab/sessions/${DEBATE_SESSION.session_id}/consensus`,
      method: 'GET',
      handler: () => {
        refetchCount += 1;
        return jsonResponse({ agreed: true, summary: 'Mostly aligned', dissenter: 'agent:critic' });
      },
    };
    // consensusRoute must come FIRST: buildFetchMock's route matching is
    // first-match-wins, and sessionRoutes() already defines its own default
    // consensus handler that would otherwise shadow this one.
    vi.mocked(globalThis.fetch).mockImplementation(
      buildFetchMock([consensusRoute, ...sessionRoutes(DEBATE_SESSION)])
    );
    await userEvent.click(screen.getByText('Refresh'));
    await waitFor(() => expect(refetchCount).toBeGreaterThan(0));
  });

  test('consensus card shows the no-consensus state', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      buildFetchMock(sessionRoutes(DEBATE_SESSION, { consensus: { agreed: false, summary: '' } }))
    );
    await openPanelFor(DEBATE_SESSION);
    expect(await screen.findByText('No consensus yet')).toBeInTheDocument();
  });

  test('operations log renders entries in reverse order and an empty state', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(buildFetchMock(sessionRoutes(DEBATE_SESSION)));
    await openPanelFor(DEBATE_SESSION);
    expect(await screen.findByText('No operations yet')).toBeInTheDocument();
  });

  test('operations log renders non-empty entries newest first', async () => {
    const ops = [
      { operation_id: 'o1', version: 1, author: 'human:lead', operation: { content: 'first' }, created_at: new Date(Date.now() - 120_000).toISOString() },
      { operation_id: 'o2', version: 2, author: 'agent:critic', operation: { content: 'second' }, created_at: new Date(Date.now() - 7_200_000).toISOString() },
    ];
    vi.spyOn(globalThis, 'fetch').mockImplementation(buildFetchMock(sessionRoutes(DEBATE_SESSION, { operations: ops })));
    await openPanelFor(DEBATE_SESSION);

    const versions = screen.getAllByText(/^v\d$/).map((el) => el.textContent);
    expect(versions[0]).toBe('v2');
    expect(versions[versions.length - 1]).toBe('v1');
  });

  test('exports the session as markdown without throwing', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(buildFetchMock(sessionRoutes(DEBATE_SESSION)));
    const createObjectURL = vi.fn(() => 'blob:mock');
    const revokeObjectURL = vi.fn();
    // @ts-expect-error jsdom does not implement these
    URL.createObjectURL = createObjectURL;
    // @ts-expect-error jsdom does not implement these
    URL.revokeObjectURL = revokeObjectURL;
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    await openPanelFor(DEBATE_SESSION);
    await userEvent.click(screen.getByText('Export'));

    expect(createObjectURL).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalled();
    expect(clickSpy).toHaveBeenCalled();
  });

  test('close button unmounts the live panel and returns to the list view', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(buildFetchMock(sessionRoutes(DEBATE_SESSION)));
    await openPanelFor(DEBATE_SESSION);
    await userEvent.click(screen.getByText('Close'));
    expect(screen.queryByTestId('live-session')).not.toBeInTheDocument();
    expect(await screen.findByText(DEBATE_SESSION.name)).toBeInTheDocument();
  });

  test('handles presence_join and presence_leave websocket messages', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(buildFetchMock(sessionRoutes(DEBATE_SESSION)));
    await openPanelFor(DEBATE_SESSION);

    await waitFor(() => expect(hoisted.onMessage).toBeDefined());
    fireEvent(window, new Event('noop')); // ensure any pending effects flush before act below

    await waitFor(() => {
      hoisted.onMessage?.({ type: 'presence_join', participant: 'human:new' });
    });
    expect(await within(screen.getByTestId('presence-bar')).findByText('new')).toBeInTheDocument();

    await waitFor(() => {
      hoisted.onMessage?.({ type: 'presence_leave', participant: 'human:lead' });
    });
    expect(within(screen.getByTestId('presence-bar')).queryByText('lead')).not.toBeInTheDocument();
  });

  test(
    'shows and clears a typing indicator for participant_typing messages',
    async () => {
      vi.spyOn(globalThis, 'fetch').mockImplementation(buildFetchMock(sessionRoutes(REVIEW_SESSION)));
      await openPanelFor(REVIEW_SESSION);
      await waitFor(() => expect(hoisted.onMessage).toBeDefined());

      await waitFor(() => {
        hoisted.onMessage?.({ type: 'participant_typing', participant: 'lead' });
      });
      expect(await screen.findByText(/is typing/)).toBeInTheDocument();

      await waitFor(
        () => expect(screen.queryByText(/is typing/)).not.toBeInTheDocument(),
        { timeout: 4000 }
      );
    },
    8000
  );
});
