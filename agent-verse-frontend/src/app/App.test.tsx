import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import App from './App';

function renderApp(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('App authentication', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('av_api_key', 'stale-key');
    useAuthStore.setState({
      apiKey: 'stale-key',
      tenantId: 'tenant-1',
      plan: 'free',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('clears persisted auth when the stored API key is rejected', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ error: { message: 'Missing or invalid API key.' } }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    renderApp('/agents');

    await waitFor(() => expect(screen.getByText(/sign in to your tenant/i)).toBeInTheDocument());
    expect(localStorage.getItem('av_api_key')).toBeNull();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});
