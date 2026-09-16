import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { MorningBrief } from './MorningBrief';

// A representative brief payload from GET /v1/org/{id}/brief/morning.
const BRIEF = {
  org_id: 'org-1',
  org_name: 'Acme Robotics',
  overall_health: 'healthy',
  active_missions: 3,
  active_teams: 2,
  pending_approvals: 1,
  priorities: [{ urgency: 'critical', text: 'Ship the launch release' }],
  risks: [{ severity: 'high', text: 'Budget nearly exhausted' }],
  items_needing_attention: 2,
};

/** Stub fetch by URL; `override` replaces the /brief/morning response. */
function mockFetch(override?: () => Response | Promise<Response>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/brief/morning')) {
      if (override) return override();
      return new Response(JSON.stringify(BRIEF), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderBrief(props: { compact?: boolean } = {}) {
  // retryDelay: 0 keeps the hook's hardcoded `retry: 1` from stalling the error test.
  const qc = new QueryClient({ defaultOptions: { queries: { retryDelay: 0 } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <MorningBrief orgId="org-1" {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('MorningBrief', () => {
  test('renders the org greeting, health badge and stat counts when the brief loads', async () => {
    mockFetch();
    renderBrief();
    // Greeting appends the first word of the org name; the exact greeting varies
    // with the clock, so match on the org name portion.
    expect(await screen.findByText(/,\s*Acme/)).toBeInTheDocument();
    expect(await screen.findByText(/HEALTHY/)).toBeInTheDocument();
    expect(screen.getByText('Missions')).toBeInTheDocument();
    expect(screen.getByText('Teams')).toBeInTheDocument();
    expect(screen.getByText('Approvals')).toBeInTheDocument();
  });

  test('renders the priorities and risks sections from the brief', async () => {
    mockFetch();
    renderBrief();
    expect(await screen.findByText("Today's Priorities")).toBeInTheDocument();
    expect(screen.getByText('Ship the launch release')).toBeInTheDocument();
    expect(screen.getByText('Risks')).toBeInTheDocument();
    expect(screen.getByText('Budget nearly exhausted')).toBeInTheDocument();
  });

  test('shows the all-clear recommendation when nothing needs attention', async () => {
    mockFetch(() =>
      new Response(
        JSON.stringify({ ...BRIEF, priorities: [], risks: [], items_needing_attention: 0 }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    renderBrief();
    expect(
      await screen.findByText(/Organisation running smoothly\. No immediate action required\./i),
    ).toBeInTheDocument();
    // With empty arrays those sections are omitted.
    expect(screen.queryByText("Today's Priorities")).not.toBeInTheDocument();
    expect(screen.queryByText('Risks')).not.toBeInTheDocument();
  });

  test('shows the generating state while the brief is loading', async () => {
    // A fetch that never resolves keeps the query in its loading state.
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise<Response>(() => {}));
    renderBrief();
    expect(await screen.findByText(/Generating brief…/i)).toBeInTheDocument();
    expect(screen.getByText(/Loading brief…/i)).toBeInTheDocument();
  });

  test('renders the unavailable state when the brief request fails', async () => {
    mockFetch(() =>
      new Response(JSON.stringify({ detail: 'boom' }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    renderBrief();
    expect(
      await screen.findByText(/Brief unavailable — check API connectivity\./i),
    ).toBeInTheDocument();
  });

  test('compact mode starts collapsed and reveals the content on expand', async () => {
    mockFetch();
    renderBrief({ compact: true });
    // Collapsed: the panel body (and its stats) is not mounted yet.
    expect(screen.queryByText('Missions')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Expand morning brief/i }));
    expect(await screen.findByText('Missions')).toBeInTheDocument();
  });
});
