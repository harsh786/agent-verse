/**
 * PromptVariantsPage tests.
 *
 * Loads variants from GET /intelligence/prompt-variants (via apiFetch), groups
 * them by key, and supports create / promote / delete / copy plus a per-key
 * filter that re-queries GET /intelligence/prompt-variants?key=:key. Covers
 * loading, error, empty, list rendering and every mutation with real endpoint
 * assertions (field names match the real backend contract in
 * app/api/enterprise.py: id, key, name, prompt_text, is_control, run_count,
 * mean_score, p95_score, promoted_at).
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { PromptVariantsPage } from './PromptVariantsPage';

const VARIANTS = [
  {
    id: 'var-a',
    key: 'system_prompt',
    name: 'Concise variant',
    prompt_text: 'You are a concise assistant that answers briefly.',
    is_control: true,
    run_count: 1200,
    mean_score: 0.62,
    p95_score: 0.9,
    promoted_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 'var-b',
    key: 'system_prompt',
    name: 'Verbose variant',
    prompt_text: 'You are a thorough assistant that explains in detail.',
    is_control: false,
    run_count: 800,
    mean_score: 0.38,
    p95_score: 0.7,
    promoted_at: null,
  },
];

function jsonResponse(body: unknown, status = 200) {
  return new Response(typeof body === 'string' ? body : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function mockFetch(variants = VARIANTS) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
    const method = (init?.method ?? 'GET').toUpperCase();
    if (method === 'GET') return jsonResponse(variants);
    return jsonResponse({ status: 'ok' });
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <PromptVariantsPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('PromptVariantsPage', () => {
  beforeEach(() => {
    useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      configurable: true,
    });
  });
  afterEach(() => vi.restoreAllMocks());

  test('renders heading and New Variant button', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByRole('heading', { name: /Prompt Variants/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /New Variant/i })).toBeInTheDocument();
  });

  test('renders variant cards with key, name, prompt preview, and win rate', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByText('Concise variant')).toBeInTheDocument();
    expect(screen.getByText('Verbose variant')).toBeInTheDocument();
    expect(screen.getByText(/You are a concise assistant/)).toBeInTheDocument();
    expect(screen.getByText(/62% win rate/)).toBeInTheDocument();
    // Active badge shows on the control (promoted) variant.
    expect(screen.getByText('Active')).toBeInTheDocument();
  });

  test('shows the empty state when there are no variants', async () => {
    mockFetch([]);
    renderPage();
    expect(await screen.findByText(/No prompt variants yet/i)).toBeInTheDocument();
  });

  test('shows the error state when the request fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ detail: 'boom' }, 500));
    renderPage();
    expect(await screen.findByText(/Failed to load prompt variants/i)).toBeInTheDocument();
  });

  test('key filter chips re-query with the key as a query param', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('Concise variant');
    // Chip renders as "system_prompt (2)".
    await userEvent.click(screen.getByRole('button', { name: /system_prompt/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u]) => /\/intelligence\/prompt-variants\?key=system_prompt$/.test(String(u)))).toBe(true)
    );
  });

  test('promoting an inactive variant POSTs to the promote endpoint', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('Verbose variant');
    await userEvent.click(screen.getByTitle('Promote to active'));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            /\/intelligence\/prompt-variants\/var-b\/promote$/.test(String(u)) &&
            (i as RequestInit)?.method === 'POST'
        )
      ).toBe(true)
    );
  });

  test('deleting a variant fires a DELETE for its id', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('Concise variant');
    await userEvent.click(screen.getAllByTitle('Delete variant')[0]);
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            /\/intelligence\/prompt-variants\/var-a$/.test(String(u)) &&
            (i as RequestInit)?.method === 'DELETE'
        )
      ).toBe(true)
    );
  });

  test('copy button writes the variant prompt text to the clipboard', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('Concise variant');
    await userEvent.click(screen.getAllByTitle('Copy content')[0]);
    await waitFor(() =>
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
        'You are a concise assistant that answers briefly.'
      )
    );
  });

  test('create modal is gated then POSTs a new variant', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('Concise variant');
    await userEvent.click(screen.getByRole('button', { name: /New Variant/i }));

    expect(await screen.findByText('New Prompt Variant')).toBeInTheDocument();
    const createBtn = screen.getByRole('button', { name: /Create Variant/i });
    expect(createBtn).toBeDisabled();

    await userEvent.type(screen.getByPlaceholderText('system_prompt'), 'planner_prompt');
    await userEvent.type(screen.getByPlaceholderText(/helpful assistant/i), 'You plan carefully.');
    expect(createBtn).toBeEnabled();
    await userEvent.click(createBtn);

    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => {
          if (!/\/intelligence\/prompt-variants$/.test(String(u)) || (i as RequestInit)?.method !== 'POST') {
            return false;
          }
          const body = JSON.parse(String((i as RequestInit).body));
          return body.key === 'planner_prompt' && body.prompt_text === 'You plan carefully.';
        })
      ).toBe(true)
    );
  });
});
