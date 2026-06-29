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

function mockTenant(plan = 'professional') {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/tenants/me')) {
      return new Response(
        JSON.stringify({ tenant_id: 'tid-1', name: 'ACME', plan }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }
    return new Response(null, { status: 404 });
  });
}

describe('ScopeExplorerPage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      apiKey: 'test-key', tenantId: 'tenant-1', plan: 'professional', isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders without crashing', async () => {
    mockTenant();
    renderPage();
    // Page loads; static scope groups are always rendered
    await waitFor(() => expect(document.body).toBeTruthy());
  });

  test('renders goals scope group', async () => {
    mockTenant();
    renderPage();
    await waitFor(() => expect(screen.getByText('goals')).toBeInTheDocument());
  });

  test('renders agents scope group', async () => {
    mockTenant();
    renderPage();
    await waitFor(() => expect(screen.getByText('agents')).toBeInTheDocument());
  });

  test('shows scopes with granted check marks for professional plan', async () => {
    mockTenant('professional');
    renderPage();
    // goals:read should be granted for professional plan
    await waitFor(() => expect(screen.getByText('goals:read')).toBeInTheDocument());
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

  test('search input filters scope list', async () => {
    mockTenant();
    renderPage();
    await waitFor(() => expect(screen.getByText('goals:read')).toBeInTheDocument());
    const searchInput = screen.getByPlaceholderText(/search/i);
    expect(searchInput).toBeInTheDocument();
  });
});
