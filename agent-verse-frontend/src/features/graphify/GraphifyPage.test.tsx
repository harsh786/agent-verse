import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { GraphifyPage } from './GraphifyPage';

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
});
