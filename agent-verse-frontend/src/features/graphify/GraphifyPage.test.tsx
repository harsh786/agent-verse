import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { GraphifyPage } from './GraphifyPage';

const mocks = vi.hoisted(() => ({
  capturedOnClose: null as (() => void) | null,
  capturedOnComplete: null as (() => void) | null,
}));

vi.mock('@/features/org/components/GraphifyProgress', () => ({
  GraphifyProgress: (p: { orgId: string; onClose?: () => void; onComplete?: () => void }) => {
    mocks.capturedOnClose = p.onClose ?? null;
    mocks.capturedOnComplete = p.onComplete ?? null;
    return (
      <div data-testid="graphify-progress">
        <span>progress-for-{p.orgId}</span>
        <button onClick={() => p.onClose?.()}>progress-close</button>
        <button onClick={() => p.onComplete?.()}>progress-complete</button>
      </div>
    );
  },
}));

vi.mock('@/features/knowledge-graph/InteractiveKnowledgeGraph', () => ({
  InteractiveKnowledgeGraph: ({ height }: { height?: number }) => (
    <div data-testid="interactive-knowledge-graph">graph-height-{height}</div>
  ),
}));

const ORGS = [
  { id: 'org-abcdef123456', name: 'Acme Corp' },
  { id: 'org-987654zyxwvu', name: 'Globex' },
];

function mockOrgs(body: unknown = ORGS, status = 200) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/v1/org'))
      return new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <GraphifyPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
  mocks.capturedOnClose = null;
  mocks.capturedOnComplete = null;
});
afterEach(() => vi.restoreAllMocks());

describe('GraphifyPage', () => {
  test('renders the page header immediately (before orgs resolve)', () => {
    mockOrgs();
    renderPage();
    // Header is static — present on first paint while the org query is loading.
    expect(screen.getByRole('heading', { name: 'Graphify' })).toBeInTheDocument();
    expect(
      screen.getByText(/Transform your org's knowledge into a glowing, interactive knowledge graph/i),
    ).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Select Organisation' })).toBeInTheDocument();
  });

  test('lists orgs and auto-selects the first, revealing the Start Graphify action', async () => {
    mockOrgs();
    renderPage();
    expect(await screen.findByText('Acme Corp')).toBeInTheDocument();
    expect(screen.getByText('Globex')).toBeInTheDocument();
    // The first org is auto-selected on load, so the Start button appears.
    expect(await screen.findByRole('button', { name: /Start Graphify/i })).toBeInTheDocument();
  });

  test('shows the no-orgs hint when the list is empty', async () => {
    mockOrgs([]);
    renderPage();
    expect(await screen.findByText(/No organisations found/i)).toBeInTheDocument();
    // With no selection there is no Start button.
    expect(screen.queryByRole('button', { name: /Start Graphify/i })).not.toBeInTheDocument();
  });

  test('falls back to the empty state when the org request fails (500)', async () => {
    mockOrgs({ error: 'boom' }, 500);
    renderPage();
    // fetchOrgs returns [] on a non-ok response, so the empty hint is shown.
    expect(await screen.findByText(/No organisations found/i)).toBeInTheDocument();
  });

  test('reads orgs from a wrapped { organizations: [...] } payload', async () => {
    mockOrgs({ organizations: [{ id: 'org-wrapped00001', name: 'Initech' }] });
    renderPage();
    expect(await screen.findByText('Initech')).toBeInTheDocument();
  });

  test('reads orgs from a wrapped { data: [...] } payload', async () => {
    mockOrgs({ data: [{ id: 'org-data0000001', name: 'Umbrella' }] });
    renderPage();
    expect(await screen.findByText('Umbrella')).toBeInTheDocument();
  });

  test('clicking a different org selects it and resets running/done state', async () => {
    const user = userEvent.setup();
    mockOrgs();
    renderPage();
    await screen.findByText('Acme Corp');

    // Start Graphify against the auto-selected first org.
    await user.click(await screen.findByRole('button', { name: /Start Graphify/i }));
    expect(await screen.findByTestId('graphify-progress')).toBeInTheDocument();
    expect(screen.getByText('progress-for-org-abcdef123456')).toBeInTheDocument();

    // Switching orgs while running should stop the run and clear "done".
    await user.click(screen.getByText('Globex'));
    expect(screen.queryByTestId('graphify-progress')).not.toBeInTheDocument();
    expect(await screen.findByRole('button', { name: /Start Graphify/i })).toBeInTheDocument();
  });

  test('starting a run shows GraphifyProgress, and onClose stops the run', async () => {
    const user = userEvent.setup();
    mockOrgs();
    renderPage();
    await user.click(await screen.findByRole('button', { name: /Start Graphify/i }));
    expect(await screen.findByTestId('graphify-progress')).toBeInTheDocument();

    await user.click(screen.getByText('progress-close'));
    expect(screen.queryByTestId('graphify-progress')).not.toBeInTheDocument();
    expect(await screen.findByRole('button', { name: /Start Graphify/i })).toBeInTheDocument();
  });

  test('onComplete reveals the done state, the graph viewer, and invalidates the kg-graph query', async () => {
    const user = userEvent.setup();
    mockOrgs();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <GraphifyPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await user.click(await screen.findByRole('button', { name: /Start Graphify/i }));
    await user.click(screen.getByText('progress-complete'));

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['kg-graph'] });
    expect(await screen.findByText(/Knowledge graph built/i)).toBeInTheDocument();
    expect(screen.getByTestId('interactive-knowledge-graph')).toHaveTextContent('graph-height-560');
    expect(screen.getByRole('link', { name: /Open full explorer/i })).toHaveAttribute('href', '/knowledge-graph');
    // The progress panel is gone once the run completes.
    expect(screen.queryByTestId('graphify-progress')).not.toBeInTheDocument();
  });

  test('"Run again" clears the done state and re-reveals the Start button', async () => {
    const user = userEvent.setup();
    mockOrgs();
    renderPage();
    await user.click(await screen.findByRole('button', { name: /Start Graphify/i }));
    await user.click(screen.getByText('progress-complete'));
    expect(await screen.findByText(/Knowledge graph built/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /Run again/i }));
    expect(screen.queryByText(/Knowledge graph built/i)).not.toBeInTheDocument();
    expect(await screen.findByRole('button', { name: /Start Graphify/i })).toBeInTheDocument();
  });
});
