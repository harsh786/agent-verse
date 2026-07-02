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

const MOCK_MEMORY = {
  id: 'm1',
  content: 'Remember the API key rotates monthly',
  memory_type: 'fact',
  confidence: 0.9,
  tags: ['ops'],
  created_at: '2026-06-01T00:00:00Z',
};

function mockFetch(memories: typeof MOCK_MEMORY[] = [MOCK_MEMORY]) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/memory/tool-reliability'))
      return new Response('[]', { status: 200 });
    if (url.includes('/memory/recall'))
      return new Response(
        JSON.stringify({ query: 'test', results: [{ content: 'recalled result', confidence: 0.8, memory_type: 'fact', source: '' }] }),
        { status: 200 }
      );
    if (url.includes('/memory/execution'))
      return new Response(
        JSON.stringify([{ goal_text: 'Deploy the service', success: true, recorded_at: '2026-06-01T00:00:00Z' }]),
        { status: 200 }
      );
    if (url.match(/\/memory\?/))
      return new Response(JSON.stringify(memories), { status: 200 });
    return new Response('[]', { status: 200 });
  });
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  localStorage.setItem('av_api_key', 'test-key');
  sessionStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('MemoryExplorerPage', () => {
  test('renders page heading and all main sections', async () => {
    mockFetch([]);
    renderPage();
    expect(screen.getByRole('heading', { name: /memory explorer/i })).toBeInTheDocument();
    // Section headings (h2) must be present
    await waitFor(() => {
      const headings = screen.getAllByRole('heading');
      const texts = headings.map(h => h.textContent?.toLowerCase() ?? '');
      expect(texts.some(t => t.includes('semantic recall'))).toBe(true);
      expect(texts.some(t => t.includes('long-term memories'))).toBe(true);
      expect(texts.some(t => t.includes('tool reliability'))).toBe(true);
    });
  });

  test('lists memories from /memory with content and type badge', async () => {
    mockFetch([MOCK_MEMORY]);
    renderPage();
    expect(await screen.findByText(/API key rotates monthly/)).toBeInTheDocument();
    // 'fact' appears as both a filter pill and a type badge — check at least one instance
    expect(screen.getAllByText('fact').length).toBeGreaterThanOrEqual(1);
  });

  test('shows tags as chips', async () => {
    mockFetch([MOCK_MEMORY]);
    renderPage();
    await screen.findByText(/API key rotates monthly/);
    expect(screen.getByText('#ops')).toBeInTheDocument();
  });

  test('shows empty state when no memories', async () => {
    mockFetch([]);
    renderPage();
    expect(await screen.findByText(/no memories yet/i)).toBeInTheDocument();
  });

  test('recall queries /memory/recall and shows results', async () => {
    mockFetch();
    renderPage();
    await userEvent.type(screen.getByPlaceholderText(/recall memories/i), 'keys');
    await userEvent.click(screen.getByRole('button', { name: /recall/i }));
    expect(await screen.findByText('recalled result')).toBeInTheDocument();
  });

  test('delete calls DELETE /memory/{id}', async () => {
    const fetchSpy = mockFetch([MOCK_MEMORY]);
    renderPage();
    await screen.findByText(/API key rotates monthly/);
    await userEvent.click(screen.getByRole('button', { name: /delete memory/i }));
    await waitFor(() => {
      const delCall = fetchSpy.mock.calls.find(
        ([u, i]) => /\/memory\/m1$/.test(String(u)) && (i as RequestInit)?.method === 'DELETE'
      );
      expect(delCall).toBeTruthy();
    });
  });

  test('type filter pills render', async () => {
    mockFetch([]);
    renderPage();
    await waitFor(() => expect(screen.getByRole('button', { name: /^all$/i })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /^fact$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^skill$/i })).toBeInTheDocument();
  });

  test('Add Memory button opens create modal', async () => {
    mockFetch([]);
    renderPage();
    await screen.findByText(/no memories yet/i);
    await userEvent.click(screen.getByRole('button', { name: /add/i }));
    expect(screen.getByRole('heading', { name: /add memory/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/content/i)).toBeInTheDocument();
  });

  test('tool reliability table shows correct columns', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/memory/tool-reliability'))
        return new Response(
          JSON.stringify([
            { tool_name: 'jira_search', success_count: 8, failure_count: 2, total_calls: 10, success_rate: 0.8 },
          ]),
          { status: 200 }
        );
      if (url.includes('/memory?'))
        return new Response('[]', { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    expect(await screen.findByText('jira_search')).toBeInTheDocument();
    expect(screen.getByText('10')).toBeInTheDocument(); // total_calls
    expect(screen.getByText('80%')).toBeInTheDocument();
  });

  test('execution memory section expands on click', async () => {
    mockFetch([]);
    renderPage();
    await waitFor(() => expect(screen.getByText(/execution memory/i)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/execution memory/i));
    expect(await screen.findByText('Deploy the service')).toBeInTheDocument();
  });

  test('clear all button opens confirm modal', async () => {
    mockFetch([MOCK_MEMORY]);
    renderPage();
    await screen.findByText(/API key rotates monthly/);
    await userEvent.click(screen.getByRole('button', { name: /clear all/i }));
    expect(screen.getByText(/clear all memories/i)).toBeInTheDocument();
  });
});
