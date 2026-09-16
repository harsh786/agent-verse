/**
 * Tests for AgentInspectorDrawer — a slide-over that fetches an agent's
 * inspector payload and renders Overview / Messages / Config tabs.
 *
 * The drawer calls civilizationApi.getAgentInspector (→ apiFetch → fetch), so
 * we stub globalThis.fetch by URL and assert the rendered content per tab.
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AgentInspectorDrawer } from './AgentInspectorDrawer';

const AGENT_ID = 'agent-abc-1234567890';

const INSPECTOR = {
  member: {
    reputation: 0.8, status: 'active', role: 'coordinator', depth: 2,
    budget_spent_usd: 1.234, budget_usd: 5,
    spawned_at: '2026-09-16T10:00:00Z', last_active_at: '2026-09-16T11:00:00Z',
  },
  agent_config: {
    name: 'Alpha Agent', goal_template: 'Do things',
    autonomy_mode: 'bounded-autonomous', connector_ids: ['jira', 'slack'],
  },
  recent_messages: [
    { topic: 'findings', ts: '2026-09-16T12:34:56Z', payload: { note: 'found it' } },
  ],
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function mockFetch(payload: unknown = INSPECTOR, status = 200) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/agents/')) return json(payload, status);
    return json({});
  });
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('AgentInspectorDrawer', () => {
  test('renders nothing when no agent is selected', () => {
    mockFetch();
    const { container } = render(
      <AgentInspectorDrawer civilizationId="civ-1" agentId={null} onClose={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByLabelText('Agent Inspector')).not.toBeInTheDocument();
  });

  test('overview tab renders the reputation, status, role and spend from the payload', async () => {
    mockFetch();
    render(<AgentInspectorDrawer civilizationId="civ-1" agentId={AGENT_ID} onClose={vi.fn()} />);
    // Header name comes from agent_config.name once loaded.
    expect(await screen.findByText('Alpha Agent')).toBeInTheDocument();
    expect(screen.getByText('80%')).toBeInTheDocument();
    expect(screen.getByText('active')).toBeInTheDocument();
    expect(screen.getByText('coordinator')).toBeInTheDocument();
    expect(screen.getByText('$1.234')).toBeInTheDocument();
  });

  test('shows a loading state while the inspector request is in flight', () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise(() => {}));
    render(<AgentInspectorDrawer civilizationId="civ-1" agentId={AGENT_ID} onClose={vi.fn()} />);
    expect(screen.getByText(/Loading…/i)).toBeInTheDocument();
  });

  test('switching to the Messages tab shows the bus feed', async () => {
    mockFetch();
    render(<AgentInspectorDrawer civilizationId="civ-1" agentId={AGENT_ID} onClose={vi.fn()} />);
    await screen.findByText('Alpha Agent');
    await userEvent.click(screen.getByRole('button', { name: /Messages/i }));
    expect(screen.getByText('findings')).toBeInTheDocument();
    expect(screen.getByText(/found it/)).toBeInTheDocument();
  });

  test('switching to the Config tab shows config fields and connector chips', async () => {
    mockFetch();
    render(<AgentInspectorDrawer civilizationId="civ-1" agentId={AGENT_ID} onClose={vi.fn()} />);
    await screen.findByText('Alpha Agent');
    await userEvent.click(screen.getByRole('button', { name: /Config/i }));
    expect(screen.getByText('Do things')).toBeInTheDocument();
    expect(screen.getByText('jira')).toBeInTheDocument();
    expect(screen.getByText('slack')).toBeInTheDocument();
  });

  test('the close button invokes onClose', async () => {
    mockFetch();
    const onClose = vi.fn();
    render(<AgentInspectorDrawer civilizationId="civ-1" agentId={AGENT_ID} onClose={onClose} />);
    await screen.findByText('Alpha Agent');
    await userEvent.click(screen.getByLabelText('Close inspector'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('renders a graceful empty state when the inspector request fails', async () => {
    mockFetch({ detail: 'boom' }, 500);
    render(<AgentInspectorDrawer civilizationId="civ-1" agentId={AGENT_ID} onClose={vi.fn()} />);
    expect(await screen.findByText(/No member data available\./i)).toBeInTheDocument();
  });
});
