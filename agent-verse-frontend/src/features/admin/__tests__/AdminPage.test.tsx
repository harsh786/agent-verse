import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import AdminPage from '../AdminPage';

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderAdminPage() {
  render(
    <QueryClientProvider client={makeQC()}>
      <AdminPage />
    </QueryClientProvider>
  );
}

describe('AdminPage', () => {
  beforeEach(() => {
    // Stub fetch to avoid real HTTP calls in unit tests
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ tenants: [], total: 0, active_goals: 0, total_tenants: 0 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );
  });

  it('renders without crashing', () => {
    renderAdminPage();
    expect(screen.getByText('Platform Administration')).toBeTruthy();
  });

  it('renders the page subtitle', () => {
    renderAdminPage();
    expect(screen.getByText(/Manage tenants, plans, and platform health/i)).toBeTruthy();
  });

  it('renders the Tenants section heading', () => {
    renderAdminPage();
    expect(screen.getByText('Tenants')).toBeTruthy();
  });

  it('renders table headers after data loads', async () => {
    renderAdminPage();
    // Table is only rendered once isLoading becomes false (query resolves)
    await waitFor(() => {
      expect(screen.getByText('Tenant ID')).toBeTruthy();
    });
    expect(screen.getByText('Plan')).toBeTruthy();
    expect(screen.getByText('Actions')).toBeTruthy();
  });
});
