import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { TemplateLibraryPage } from './TemplateLibraryPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <TemplateLibraryPage />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

const TEMPLATE = {
  id: 't1', name: 'Deploy Service', description: 'Deploy a microservice', goal_text: 'Deploy {{service}} to {{env}}',
  domain: 'devops', parameters: [{ name: 'service', description: '', required: true }, { name: 'env', description: '', required: true }],
  use_count: 5, version: 1, created_at: new Date().toISOString(),
};

function mockFetch(templates = [TEMPLATE]) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    if (String(input).includes('/templates'))
      return new Response(JSON.stringify(templates), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('[]', { status: 200 });
  });
}

beforeEach(() => {
  sessionStorage.setItem('av_api_key', 'test-key');
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('TemplateLibraryPage', () => {
  test('renders Template Library heading', () => {
    mockFetch([]);
    renderPage();
    expect(screen.getByRole('heading', { name: /template library/i })).toBeInTheDocument();
  });

  test('shows New Template button', () => {
    mockFetch([]);
    renderPage();
    expect(screen.getByRole('button', { name: /new template/i })).toBeInTheDocument();
  });

  test('lists templates', async () => {
    mockFetch([TEMPLATE]);
    renderPage();
    expect(await screen.findByText('Deploy Service')).toBeInTheDocument();
  });

  test('shows empty state when no templates', async () => {
    mockFetch([]);
    renderPage();
    // The empty state renders when filtered.length === 0
    expect(await screen.findByText(/create your first template/i)).toBeInTheDocument();
  });

  test('shows domain filter pills', async () => {
    mockFetch([]);
    renderPage();
    await waitFor(() => expect(screen.getByRole('button', { name: /devops/i })).toBeInTheDocument());
  });

  test('search input filters templates via server', async () => {
    // Since search is now server-side, the second fetch (with search param) returns empty
    let fetchCallCount = 0;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      fetchCallCount++;
      const url = String(input);
      if (url.includes('/templates')) {
        // First call (no search) returns a template; subsequent calls (with search) return empty
        const hasSearch = url.includes('search=') || url.includes('q=');
        const body = hasSearch ? '[]' : JSON.stringify([TEMPLATE]);
        return new Response(body, { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await screen.findByText('Deploy Service');
    const searchInput = screen.getByLabelText(/search templates/i);
    await userEvent.clear(searchInput);
    await userEvent.type(searchInput, 'xyz-no-match');
    // After debounce, query with search param returns empty — template disappears
    await waitFor(() => expect(screen.queryByText('Deploy Service')).not.toBeInTheDocument(), { timeout: 3000 });
  });

  test('New Template button opens create modal', async () => {
    mockFetch([]);
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /new template/i }));
    expect(screen.getByRole('heading', { name: /new template/i })).toBeInTheDocument();
  });

  test('template card has Edit and Delete buttons', async () => {
    mockFetch([TEMPLATE]);
    renderPage();
    await screen.findByText('Deploy Service');
    expect(screen.getByRole('button', { name: /edit template/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /delete template/i })).toBeInTheDocument();
  });
});
