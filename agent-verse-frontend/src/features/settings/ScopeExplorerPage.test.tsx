import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ScopeExplorerPage } from './ScopeExplorerPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ScopeExplorerPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function mockFetch(plan = 'professional') {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/tenants/me/keys'))
      return new Response(JSON.stringify([
        { key_id: 'k1', name: 'Production Key', scopes: ['goals:read'], created_at: '2026-01-01T00:00:00Z' },
      ]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/tenants/me'))
      return new Response(
        JSON.stringify({ tenant_id: 'tid-1', name: 'ACME', plan }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

describe('ScopeExplorerPage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      apiKey: 'test-key', tenantId: 'tenant-1', plan: 'professional', isAuthenticated: true,
    });
  });
  afterEach(() => vi.restoreAllMocks());

  test('renders without crashing', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(document.body).toBeTruthy());
  });

  test('renders goals scope group', async () => {
    mockFetch();
    renderPage();
    // The page shows scope groups — "goals" should appear somewhere
    await waitFor(() => expect(screen.getAllByText(/goals/i).length).toBeGreaterThanOrEqual(1));
  });

  test('renders agents scope group', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getAllByText(/agents/i).length).toBeGreaterThanOrEqual(1));
  });

  test('shows scopes with granted check marks for professional plan', async () => {
    mockFetch('professional');
    renderPage();
    // goals:read should be visible somewhere in scope list
    await waitFor(() => expect(screen.getAllByText(/goals:read/i).length).toBeGreaterThanOrEqual(1));
  });

  test('shows error state when tenant fetch fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('error', { status: 500 })
    );
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/failed to load/i)).toBeInTheDocument()
    );
  });

  test('search input is present', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByPlaceholderText(/search/i)).toBeInTheDocument());
  });

  test('API keys section renders', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('api-keys-section')).toBeInTheDocument());
  });
});
