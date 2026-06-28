import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { MemoryExplorerPage } from './MemoryExplorerPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <MemoryExplorerPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('MemoryExplorerPage', () => {
  test('lists memories from /memory', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/memory/tool-reliability'))
        return new Response('[]', { status: 200 });
      if (url.includes('/memory?'))
        return new Response(
          JSON.stringify([
            {
              id: 'm1',
              content: 'Remember the API key rotates monthly',
              memory_type: 'fact',
              confidence: 0.9,
              tags: ['ops'],
              created_at: '2026-06-01T00:00:00Z',
            },
          ]),
          { status: 200 }
        );
      return new Response('[]', { status: 200 });
    });
    renderPage();
    expect(await screen.findByText(/API key rotates monthly/)).toBeInTheDocument();
  });

  test('shows empty state when no memories', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/memory/tool-reliability'))
        return new Response('[]', { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    expect(await screen.findByText(/No memories yet/i)).toBeInTheDocument();
  });

  test('recall queries /memory/recall and shows results', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/memory/recall'))
        return new Response(
          JSON.stringify({
            query: 'keys',
            results: [{ content: 'rotate keys', confidence: 0.7, memory_type: 'fact', source: 'g1' }],
          }),
          { status: 200 }
        );
      if (url.includes('/memory/tool-reliability'))
        return new Response('[]', { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.type(screen.getByPlaceholderText(/recall/i), 'keys');
    await userEvent.click(screen.getByRole('button', { name: /recall/i }));
    expect(await screen.findByText('rotate keys')).toBeInTheDocument();
  });

  test('delete calls DELETE /memory/{id}', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/memory/tool-reliability'))
        return new Response('[]', { status: 200 });
      if (url.match(/\/memory\/m1$/) && init?.method === 'DELETE')
        return new Response(JSON.stringify({ deleted: 'm1', status: 'ok' }), { status: 200 });
      if (url.includes('/memory?'))
        return new Response(
          JSON.stringify([
            { id: 'm1', content: 'Old note', memory_type: 'fact', confidence: 0.5, tags: [], created_at: '' },
          ]),
          { status: 200 }
        );
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await screen.findByText('Old note');
    await userEvent.click(screen.getByRole('button', { name: /delete memory/i }));
    await waitFor(() => {
      const del = f.mock.calls.find(
        ([u, i]) => /\/memory\/m1$/.test(String(u)) && (i as RequestInit)?.method === 'DELETE'
      );
      expect(del).toBeTruthy();
    });
  });
});
