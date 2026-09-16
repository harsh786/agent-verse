import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ObsidianPage } from './ObsidianPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ObsidianPage />
    </QueryClientProvider>
  );
}

const EMPTY_PAGE = { data: [], cursor: null, hasMore: false };

function mockFetch(orgsPayload: unknown = []) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/v1/org') && !url.includes('/tasks') && !url.includes('/missions') && !url.includes('/events'))
      return new Response(JSON.stringify(orgsPayload), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/knowledge-graph/export'))
      return new Response(JSON.stringify({ nodes: [], edges: [], stats: { nodes: 0, edges: 0 } }), { status: 200 });
    if (url.includes('/tasks') || url.includes('/missions') || url.includes('/events'))
      return new Response(JSON.stringify(EMPTY_PAGE), { status: 200 });
    return new Response('{}', { status: 200 });
  });
}

beforeEach(() => {
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ObsidianPage', () => {
  test('renders the Obsidian Mode heading', async () => {
    mockFetch([]);
    renderPage();
    expect(await screen.findByRole('heading', { name: /obsidian mode/i })).toBeInTheDocument();
  });

  test('shows a message when no organisations exist', async () => {
    mockFetch([]);
    renderPage();
    expect(await screen.findByText(/no organisations found/i)).toBeInTheDocument();
  });

  test('auto-selects the first org and renders the vault explorer', async () => {
    mockFetch([{ id: 'org-1', name: 'Acme Inc' }, { id: 'org-2', name: 'Other Org' }]);
    renderPage();
    expect(await screen.findByText(/obsidian vault/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /change org/i })).toBeInTheDocument();
    // Org selector should be hidden once auto-selected
    expect(screen.queryByText(/select organisation vault/i)).not.toBeInTheDocument();
  });

  test('Change org returns to the org selector, listing all orgs', async () => {
    mockFetch([{ id: 'org-1', name: 'Acme Inc' }, { id: 'org-2', name: 'Other Org' }]);
    renderPage();
    await waitFor(() => expect(screen.getByRole('button', { name: /change org/i })).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /change org/i }));
    expect(screen.getByText(/select organisation vault/i)).toBeInTheDocument();
    expect(screen.getByText('Acme Inc')).toBeInTheDocument();
    expect(screen.getByText('Other Org')).toBeInTheDocument();
  });

  test('selecting an org from the selector opens its vault', async () => {
    mockFetch([{ id: 'org-1', name: 'Acme Inc' }, { id: 'org-2', name: 'Other Org' }]);
    renderPage();
    await waitFor(() => expect(screen.getByRole('button', { name: /change org/i })).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /change org/i }));
    await userEvent.click(screen.getByText('Other Org'));
    expect(await screen.findByText(/obsidian vault/i)).toBeInTheDocument();
    expect(screen.queryByText(/select organisation vault/i)).not.toBeInTheDocument();
  });

  test('handles an org payload wrapped in a data field', async () => {
    mockFetch({ data: [{ id: 'org-9', name: 'Wrapped Org' }] });
    renderPage();
    expect(await screen.findByText(/obsidian vault/i)).toBeInTheDocument();
  });

  test('shows the empty-org message when the org fetch fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('error', { status: 500 }));
    renderPage();
    expect(await screen.findByText(/no organisations found/i)).toBeInTheDocument();
  });
});
