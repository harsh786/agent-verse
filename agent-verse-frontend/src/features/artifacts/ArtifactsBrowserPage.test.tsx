import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ArtifactsBrowserPage } from './ArtifactsBrowserPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ArtifactsBrowserPage />
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

const ARTIFACT = {
  id: 'a1',
  name: 'report.txt',
  artifact_type: 'file',
  storage_uri: 'https://cdn/x/report.txt',
  content_type: 'text/plain',
  size_bytes: 120,
  goal_id: 'g1',
  created_at: '2026-06-10T00:00:00Z',
};

describe('ArtifactsBrowserPage', () => {
  test('lists artifacts', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([ARTIFACT]), { status: 200 })
    );
    renderPage();
    expect(await screen.findByText('report.txt')).toBeInTheDocument();
  });

  test('shows loading skeleton initially', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve(new Response('[]', { status: 200 })), 500))
    );
    renderPage();
    // Skeleton renders while loading (animate-pulse divs)
    const { container } = renderPage();
    expect(container.querySelector('.animate-pulse')).toBeTruthy();
  });

  test('empty state when no artifacts', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }));
    renderPage();
    expect(await screen.findByText(/no artifacts/i)).toBeInTheDocument();
  });

  test('delete calls DELETE /artifacts/{id}', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.match(/\/artifacts\/a1$/) && init?.method === 'DELETE')
        return new Response(null, { status: 204 });
      return new Response(JSON.stringify([ARTIFACT]), { status: 200 });
    });
    renderPage();
    await screen.findByText('report.txt');
    await userEvent.click(screen.getByRole('button', { name: /delete artifact/i }));
    await waitFor(() => {
      const del = f.mock.calls.find(
        ([u, i]) => /\/artifacts\/a1$/.test(String(u)) && (i as RequestInit)?.method === 'DELETE'
      );
      expect(del).toBeTruthy();
    });
  });
});
