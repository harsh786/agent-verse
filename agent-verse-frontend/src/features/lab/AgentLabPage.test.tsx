import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AgentLabPage } from './AgentLabPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <AgentLabPage />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  sessionStorage.setItem('av_api_key', 'test-key');
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 200 }));
});
afterEach(() => vi.restoreAllMocks());

describe('AgentLabPage', () => {
  test('renders Agent Lab heading', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole('heading', { name: /agent lab/i })).toBeInTheDocument());
  });

  test('renders without crashing', async () => {
    renderPage();
    await waitFor(() => expect(document.body).toBeTruthy());
  });

  test('mounts and shows content', async () => {
    renderPage();
    await waitFor(() => {
      const headings = screen.getAllByRole('heading');
      expect(headings.length).toBeGreaterThan(0);
    });
  });
});
