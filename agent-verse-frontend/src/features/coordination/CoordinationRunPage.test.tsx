import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { coordinationApi } from './coordinationApi';
import { CoordinationRunPage } from './CoordinationRunPage';

// vi.mock factories are hoisted above top-level const declarations, so BASE_RUN
// must be created via vi.hoisted() to be available inside the factory below.
const { BASE_RUN } = vi.hoisted(() => ({
  BASE_RUN: {
    session: { session_id: 's1', tenant_id: 't1', state: 'active', next_sequence: 2, version: 1 },
    messages: { items: [{ message_id: 'm1', sequence: 1, sender_agent_id: 'planner', safe_content: 'Plan accepted' }] },
    ledger: { version: 3, task_state: 'executing', progress_summary: 'One task running' },
    moa: { items: [{ layer_index: 0, quorum_met: true, proposals: [] }] },
    camel: { items: [] },
    generative: { items: [] },
    swarm: { nodes: [{ agent_id: 'planner' }, { agent_id: 'worker' }], edges: [] },
    auction: { items: [], sealed_bid_count: 2 },
  },
}));

vi.mock('./coordinationApi', () => ({
  coordinationApi: {
    getRun: vi.fn().mockResolvedValue(BASE_RUN),
    transition: vi.fn().mockResolvedValue(undefined),
  },
}));

vi.mock('./useCoordinationStream', () => ({
  useCoordinationStream: () => ({ events: [], status: 'live', lastSequence: 1 }),
}));

function LocationDisplay() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

function renderAtSession(sessionId: string | null) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const initial = sessionId ? `/coordination/${sessionId}` : '/coordination';
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route
            path={sessionId ? '/coordination/:sessionId' : '/coordination'}
            element={
              <>
                <CoordinationRunPage />
                <LocationDisplay />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  // restoreAllMocks() must run FIRST: it resets vi.fn()s created inside the
  // vi.mock() factories above (e.g. removes .mockRejectedValueOnce) — running
  // it after re-establishing the default mockResolvedValue would wipe that
  // default back out, leaving getRun() returning undefined for every test
  // that runs after the first one that used a *Once() override.
  vi.restoreAllMocks();
  vi.mocked(coordinationApi.getRun).mockReset().mockResolvedValue(BASE_RUN);
  vi.mocked(coordinationApi.transition).mockReset().mockResolvedValue(undefined);
});

describe('CoordinationRunPage', () => {
  test('renders the causal timeline and pattern evidence', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/coordination/s1']}>
          <Routes><Route path="/coordination/:sessionId" element={<CoordinationRunPage />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByRole('heading', { name: /coordination ledger/i })).toBeInTheDocument();
    expect(await screen.findByText('Plan accepted')).toBeInTheDocument();
    expect(screen.getByText('2 sealed bids')).toBeInTheDocument();
    expect(screen.getByLabelText('Swarm topology')).toHaveTextContent('planner');
    expect(screen.getByText(/stream: live/i)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /parent and child topology/i })).toBeInTheDocument();
  });

  test('prompts for a session id when none is provided', () => {
    renderAtSession(null);
    expect(screen.getByText(/enter a session id to inspect/i)).toBeInTheDocument();
    expect(vi.mocked(coordinationApi.getRun)).not.toHaveBeenCalled();
  });

  test('shows a loading indicator while the run is being fetched', () => {
    vi.mocked(coordinationApi.getRun).mockImplementationOnce(() => new Promise(() => {}));
    renderAtSession('s1');
    expect(screen.getByRole('status')).toHaveTextContent(/loading session evidence/i);
  });

  test('shows an error state when the run fails to load', async () => {
    vi.mocked(coordinationApi.getRun).mockRejectedValueOnce(new Error('boom'));
    renderAtSession('s1');
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not be loaded/i);
  });

  test('opens a new session from the input and navigates to it', async () => {
    const user = userEvent.setup();
    renderAtSession('s1');
    await screen.findByText('Plan accepted');
    const input = screen.getByLabelText('Session ID');
    await user.clear(input);
    await user.type(input, 's2');
    await user.click(screen.getByRole('button', { name: /open/i }));
    expect(screen.getByTestId('location')).toHaveTextContent('/coordination/s2');
  });

  test('does not navigate when the session input is blank', async () => {
    const user = userEvent.setup();
    renderAtSession('s1');
    await screen.findByText('Plan accepted');
    const input = screen.getByLabelText('Session ID');
    await user.clear(input);
    await user.click(screen.getByRole('button', { name: /open/i }));
    expect(screen.getByTestId('location')).toHaveTextContent('/coordination/s1');
  });

  test('cancelling requires confirmation and triggers the transition', async () => {
    const user = userEvent.setup();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderAtSession('s1');
    await screen.findByText('Plan accepted');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(coordinationApi.transition).toHaveBeenCalledWith('s1', 'cancel', 1));
  });

  test('cancelling does nothing when the confirmation is declined', async () => {
    const user = userEvent.setup();
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderAtSession('s1');
    await screen.findByText('Plan accepted');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(coordinationApi.transition).not.toHaveBeenCalled();
  });

  test('shows a Resume action and no confirmation is required when the session is paused', async () => {
    const user = userEvent.setup();
    vi.mocked(coordinationApi.getRun).mockResolvedValue({
      ...BASE_RUN,
      session: { ...BASE_RUN.session, state: 'paused' },
    });
    const confirmSpy = vi.spyOn(window, 'confirm');
    renderAtSession('s1');
    await screen.findByText('Plan accepted');
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Resume' }));
    await waitFor(() => expect(coordinationApi.transition).toHaveBeenCalledWith('s1', 'resume', 1));
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  test('hides the Cancel/Resume controls once the session has completed', async () => {
    vi.mocked(coordinationApi.getRun).mockResolvedValue({
      ...BASE_RUN,
      session: { ...BASE_RUN.session, state: 'completed' },
    });
    renderAtSession('s1');
    await screen.findByText('Plan accepted');
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Resume' })).not.toBeInTheDocument();
  });

  test('shows an error message when the transition command fails', async () => {
    const user = userEvent.setup();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    vi.mocked(coordinationApi.transition).mockRejectedValueOnce(new Error('nope'));
    renderAtSession('s1');
    await screen.findByText('Plan accepted');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(await screen.findByRole('alert', { name: '' })).toBeInTheDocument();
    expect(await screen.findByText(/session command failed/i)).toBeInTheDocument();
  });
});
