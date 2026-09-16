import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { MissionInspectorPanel } from './MissionInspectorPanel';

const MISSION = {
  title: 'Launch the API',
  status: 'active',
  priority: 'high',
  budget_usd: 10,
  actual_cost_usd: 2.5,
  objective: 'Ship the public REST endpoints',
  tags: ['engineering', 'platform'],
};

function mockFetch(payload: unknown = MISSION) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/missions/'))
      return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPanel(props: Partial<React.ComponentProps<typeof MissionInspectorPanel>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onClose = props.onClose ?? vi.fn();
  const utils = render(
    <QueryClientProvider client={qc}>
      <MissionInspectorPanel orgId="o1" missionId="m1" agentId={null} onClose={onClose} {...props} />
    </QueryClientProvider>,
  );
  return { ...utils, onClose };
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('MissionInspectorPanel', () => {
  test('renders nothing when there is no selected mission or agent', () => {
    mockFetch();
    const { onClose } = renderPanel({ missionId: null, agentId: null });
    expect(screen.queryByRole('complementary', { name: /mission inspector/i })).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  test('renders mission status, budget, objective and department tags from the API', async () => {
    mockFetch();
    renderPanel();
    expect(await screen.findByText('Launch the API')).toBeInTheDocument();
    expect(screen.getByText('active')).toBeInTheDocument();
    expect(screen.getByText('high priority')).toBeInTheDocument();
    // budget bar shows used / total.
    expect(screen.getByText('$2.500 / $10.00')).toBeInTheDocument();
    expect(screen.getByText('Ship the public REST endpoints')).toBeInTheDocument();
    expect(screen.getByText('engineering')).toBeInTheDocument();
    expect(screen.getByText('platform')).toBeInTheDocument();
  });

  test('clicking close invokes the onClose callback', async () => {
    mockFetch();
    const { onClose } = renderPanel();
    await screen.findByText('Launch the API');
    await userEvent.click(screen.getByRole('button', { name: /close inspector/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('with only an agent selected it shows the agent placeholder state', async () => {
    mockFetch();
    renderPanel({ missionId: null, agentId: 'agent-12345678' });
    // agentId.slice(-8) → "12345678".
    expect(await screen.findByText('Select a mission to inspect details')).toBeInTheDocument();
    expect(screen.getAllByText(/12345678/).length).toBeGreaterThan(0);
  });
});
