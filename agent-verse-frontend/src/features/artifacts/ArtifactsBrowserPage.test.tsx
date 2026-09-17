import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { ArtifactsBrowserPage } from './ArtifactsBrowserPage';

const ARTIFACT = (overrides = {}) => ({
  id: 'art-001',
  name: 'report.json',
  artifact_type: 'report',
  storage_uri: 'https://example.com/report.json',
  content_type: 'application/json',
  size_bytes: 1024,
  goal_id: 'goal-abc123',
  created_at: new Date().toISOString(),
  ...overrides,
});

function mockFetch(artifacts = [ARTIFACT()]) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/artifacts'))
      return new Response(JSON.stringify(artifacts), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('[]', { status: 200 });
  });
}

function mockFetchImpl(handler: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => handler(String(input), init));
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <ArtifactsBrowserPage />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  sessionStorage.setItem('av_api_key', 'test-key');
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
  useToastStore.setState({ toasts: [] });
});
afterEach(() => vi.restoreAllMocks());

describe('ArtifactsBrowserPage', () => {
  test('renders heading', () => {
    mockFetch([]);
    renderPage();
    expect(screen.getByRole('heading', { name: /artifacts/i })).toBeInTheDocument();
  });

  test('shows loading skeletons initially', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.queryByTestId('artifact-card')).not.toBeInTheDocument();
  });

  test('displays artifact cards', async () => {
    mockFetch([ARTIFACT()]);
    renderPage();
    await waitFor(() => expect(screen.getByText('report.json')).toBeInTheDocument());
    expect(screen.getByTestId('artifact-card')).toBeInTheDocument();
  });

  test('shows empty state when no artifacts', async () => {
    mockFetch([]);
    renderPage();
    await waitFor(() => expect(screen.getByText(/no artifacts yet/i)).toBeInTheDocument());
  });

  test('search filters artifacts by name', async () => {
    mockFetch([ARTIFACT({ id: 'a1', name: 'report.json' }), ARTIFACT({ id: 'a2', name: 'screenshot.png', artifact_type: 'screenshot' })]);
    renderPage();
    await waitFor(() => screen.getByText('report.json'));
    await userEvent.type(screen.getByLabelText(/search artifacts/i), 'screenshot');
    await waitFor(() => expect(screen.getByText('screenshot.png')).toBeInTheDocument());
    await waitFor(() => expect(screen.queryByText('report.json')).not.toBeInTheDocument());
  });

  test('type filter pills render', async () => {
    mockFetch([]);
    renderPage();
    await waitFor(() => screen.getByRole('heading', { name: /artifacts/i }));
    expect(screen.getByRole('button', { name: /^all$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^image$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^screenshot$/i })).toBeInTheDocument();
  });

  test('type filter hides non-matching artifacts', async () => {
    mockFetch([
      ARTIFACT({ id: 'a1', name: 'code.py', artifact_type: 'code' }),
      ARTIFACT({ id: 'a2', name: 'photo.png', artifact_type: 'image' }),
    ]);
    renderPage();
    await waitFor(() => screen.getByText('code.py'));
    await userEvent.click(screen.getByRole('button', { name: /^image$/i }));
    expect(screen.queryByText('code.py')).not.toBeInTheDocument();
    expect(screen.getByText('photo.png')).toBeInTheDocument();
  });

  test('sort select renders', async () => {
    mockFetch([ARTIFACT()]);
    renderPage();
    await waitFor(() => screen.getByTestId('artifact-card'));
    expect(screen.getByLabelText(/sort artifacts/i)).toBeInTheDocument();
  });

  test('delete calls DELETE /artifacts/{id}', async () => {
    const fetchSpy = mockFetch([ARTIFACT()]);
    renderPage();
    await waitFor(() => screen.getByTestId('artifact-card'));
    // Open detail drawer
    await userEvent.click(screen.getByTestId('artifact-card'));
    // Click delete in drawer (first delete button)
    const deleteButtons = screen.getAllByRole('button', { name: /^delete$/i });
    await userEvent.click(deleteButtons[0]);
    // Confirm in modal (second delete button that appeared)
    await waitFor(() => expect(screen.getAllByRole('button', { name: /^delete$/i }).length).toBeGreaterThan(1));
    const confirmButtons = screen.getAllByRole('button', { name: /^delete$/i });
    await userEvent.click(confirmButtons[confirmButtons.length - 1]);
    await waitFor(() => {
      const del = fetchSpy.mock.calls.find(([u, i]) => String(u).includes('/artifacts/art-001') && (i as RequestInit)?.method === 'DELETE');
      expect(del).toBeTruthy();
    });
  });

  test('goal link is present in card', async () => {
    mockFetch([ARTIFACT()]);
    renderPage();
    await waitFor(() => screen.getByTestId('artifact-card'));
    expect(screen.getByTestId('goal-link')).toBeInTheDocument();
  });

  test('clicking goal link does not open the detail drawer', async () => {
    mockFetch([ARTIFACT()]);
    renderPage();
    await waitFor(() => screen.getByTestId('artifact-card'));
    await userEvent.click(screen.getByTestId('goal-link'));
    expect(screen.queryByLabelText(/details for/i)).not.toBeInTheDocument();
  });

  test('keyboard Enter on a card opens the detail drawer', async () => {
    mockFetch([ARTIFACT()]);
    renderPage();
    await waitFor(() => screen.getByTestId('artifact-card'));
    screen.getByTestId('artifact-card').focus();
    fireEvent.keyDown(screen.getByTestId('artifact-card'), { key: 'Enter' });
    expect(await screen.findByLabelText(/details for report\.json/i)).toBeInTheDocument();
  });

  test('shows error state when the artifacts request fails', async () => {
    mockFetchImpl(async () => new Response(JSON.stringify({ error: { message: 'boom' } }), { status: 500 }));
    renderPage();
    await waitFor(() => expect(screen.getByText(/failed to load artifacts/i)).toBeInTheDocument());
  });

  test('refresh button re-triggers the artifacts query', async () => {
    const fetchSpy = mockFetch([ARTIFACT()]);
    renderPage();
    await waitFor(() => screen.getByTestId('artifact-card'));
    const callsBefore = fetchSpy.mock.calls.length;
    await userEvent.click(screen.getByRole('button', { name: /refresh artifacts/i }));
    await waitFor(() => expect(fetchSpy.mock.calls.length).toBeGreaterThan(callsBefore));
  });

  test('sort select reorders cards (largest/smallest/oldest)', async () => {
    mockFetch([
      ARTIFACT({ id: 'a1', name: 'small.json', size_bytes: 10, created_at: new Date(Date.now() - 60_000).toISOString() }),
      ARTIFACT({ id: 'a2', name: 'big.json', size_bytes: 9000, created_at: new Date().toISOString() }),
    ]);
    renderPage();
    await waitFor(() => screen.getByText('small.json'));

    const getCardNames = () => screen.getAllByTestId('artifact-card').map((c) => within(c).getByTitle(/\.json$/).textContent);

    await userEvent.selectOptions(screen.getByLabelText(/sort artifacts/i), 'largest');
    await waitFor(() => expect(getCardNames()[0]).toBe('big.json'));

    await userEvent.selectOptions(screen.getByLabelText(/sort artifacts/i), 'smallest');
    await waitFor(() => expect(getCardNames()[0]).toBe('small.json'));

    await userEvent.selectOptions(screen.getByLabelText(/sort artifacts/i), 'oldest');
    await waitFor(() => expect(getCardNames()[0]).toBe('small.json'));
  });

  test('group by goal groups cards under goal headers, including "(No goal)"', async () => {
    mockFetch([
      ARTIFACT({ id: 'a1', name: 'one.json', goal_id: 'goal-aaaaaaaaaaaaaaaa' }),
      ARTIFACT({ id: 'a2', name: 'two.json', goal_id: 'goal-bbbbbbbbbbbbbbbb' }),
      ARTIFACT({ id: 'a3', name: 'orphan.json', goal_id: undefined }),
    ]);
    renderPage();
    await waitFor(() => screen.getByText('one.json'));

    await userEvent.click(screen.getByRole('button', { name: /group by goal/i }));
    expect(await screen.findByText('(No goal)')).toBeInTheDocument();
    expect(screen.getAllByText(/^Goal:/).length).toBe(2);

    // toggle back to flat list
    await userEvent.click(screen.getByRole('button', { name: /switch to flat list/i }));
    await waitFor(() => expect(screen.queryByText('(No goal)')).not.toBeInTheDocument());
  });

  test('drawer image viewer renders and tolerates load errors', async () => {
    mockFetch([ARTIFACT({ artifact_type: 'image', content_type: 'image/png', storage_uri: 'https://example.com/pic.png' })]);
    renderPage();
    await waitFor(() => screen.getByTestId('artifact-card'));
    await userEvent.click(screen.getByTestId('artifact-card'));
    const img = await screen.findByAltText('report.json');
    fireEvent.error(img);
    expect(img.style.display).toBe('none');
  });

  test('drawer JSON viewer loads and renders content', async () => {
    mockFetchImpl(async (url) => {
      if (url.includes('/artifacts')) return new Response(JSON.stringify([ARTIFACT()]), { status: 200 });
      return new Response(JSON.stringify({ hello: 'world' }), { status: 200 });
    });
    renderPage();
    await waitFor(() => screen.getByTestId('artifact-card'));
    await userEvent.click(screen.getByTestId('artifact-card'));
    await waitFor(() => expect(screen.getByText(/"hello"/)).toBeInTheDocument());
  });

  test('drawer JSON viewer shows a fallback link when the fetch fails', async () => {
    mockFetchImpl(async (url) => {
      if (url.includes('/artifacts')) return new Response(JSON.stringify([ARTIFACT()]), { status: 200 });
      throw new Error('network down');
    });
    renderPage();
    await waitFor(() => screen.getByTestId('artifact-card'));
    await userEvent.click(screen.getByTestId('artifact-card'));
    await waitFor(() => expect(screen.getByText(/cannot load json preview/i)).toBeInTheDocument());
    expect(screen.getByRole('link', { name: /open in new tab/i })).toBeInTheDocument();
  });

  test('drawer text viewer renders an iframe', async () => {
    mockFetch([ARTIFACT({ content_type: 'text/plain', storage_uri: 'https://example.com/notes.txt' })]);
    renderPage();
    await waitFor(() => screen.getByTestId('artifact-card'));
    await userEvent.click(screen.getByTestId('artifact-card'));
    const drawer = await screen.findByLabelText(/details for report\.json/i);
    expect(drawer.querySelector('iframe[title="report.json"]')).not.toBeNull();
  });

  test('drawer falls back for unrecognized content types', async () => {
    mockFetch([ARTIFACT({ content_type: 'application/octet-stream' })]);
    renderPage();
    await waitFor(() => screen.getByTestId('artifact-card'));
    await userEvent.click(screen.getByTestId('artifact-card'));
    expect(await screen.findByText(/preview not available/i)).toBeInTheDocument();
  });

  test('download link appears for http storage URIs, disabled otherwise', async () => {
    mockFetch([ARTIFACT({ storage_uri: 's3://bucket/report.json' })]);
    renderPage();
    await waitFor(() => screen.getByTestId('artifact-card'));
    await userEvent.click(screen.getByTestId('artifact-card'));
    const downloadBtn = await screen.findByRole('button', { name: /^download$/i });
    expect(downloadBtn).toBeDisabled();
  });

  test('copy URI button copies to clipboard and shows a toast', async () => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
    mockFetch([ARTIFACT()]);
    renderPage();
    await waitFor(() => screen.getByTestId('artifact-card'));
    await userEvent.click(screen.getByTestId('artifact-card'));
    await userEvent.click(screen.getByRole('button', { name: /copy uri/i }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('https://example.com/report.json');
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.message.includes('URI copied'))).toBe(true)
    );
  });

  test('use as input copies uri, toasts, and schedules navigation', async () => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
    mockFetch([ARTIFACT()]);
    renderPage();
    await waitFor(() => screen.getByTestId('artifact-card'));
    await userEvent.click(screen.getByTestId('artifact-card'));
    await userEvent.click(screen.getByRole('button', { name: /use as input/i }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('https://example.com/report.json');
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.message.includes('Opening goal form'))).toBe(true)
    );
  });

  test('go to goal button in drawer is clickable', async () => {
    mockFetch([ARTIFACT()]);
    renderPage();
    await waitFor(() => screen.getByTestId('artifact-card'));
    await userEvent.click(screen.getByTestId('artifact-card'));
    const drawer = await screen.findByLabelText(/details for report\.json/i);
    await userEvent.click(within(drawer).getByRole('button', { name: /go to goal/i }));
    // Navigating away doesn't throw; the app shell keeps rendering.
    expect(screen.queryByText(/failed to load artifacts/i)).not.toBeInTheDocument();
  });

  test('cancelling the delete confirmation keeps the artifact', async () => {
    const fetchSpy = mockFetch([ARTIFACT()]);
    renderPage();
    await waitFor(() => screen.getByTestId('artifact-card'));
    await userEvent.click(screen.getByTestId('artifact-card'));
    await userEvent.click(screen.getByRole('button', { name: /^delete$/i }));
    await waitFor(() => expect(screen.getByRole('button', { name: /^cancel$/i })).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /^cancel$/i }));
    await waitFor(() => expect(screen.queryByRole('button', { name: /^cancel$/i })).not.toBeInTheDocument());
    expect(fetchSpy.mock.calls.some(([, i]) => (i as RequestInit)?.method === 'DELETE')).toBe(false);
  });

  test('delete failure shows an error toast', async () => {
    mockFetchImpl(async (url, init) => {
      const method = (init as RequestInit | undefined)?.method;
      if (url.includes('/artifacts/art-001') && method === 'DELETE') {
        return new Response(JSON.stringify({ error: { message: 'cannot delete' } }), { status: 500 });
      }
      if (url.includes('/artifacts')) return new Response(JSON.stringify([ARTIFACT()]), { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await waitFor(() => screen.getByTestId('artifact-card'));
    await userEvent.click(screen.getByTestId('artifact-card'));
    await userEvent.click(screen.getByRole('button', { name: /^delete$/i }));
    const confirmButtons = await screen.findAllByRole('button', { name: /^delete$/i });
    await userEvent.click(confirmButtons[confirmButtons.length - 1]);
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.kind === 'error' && t.message.includes('Delete failed'))).toBe(true)
    );
  });

  test('pagination appears when total exceeds the page size and page changes update the query', async () => {
    const items = Array.from({ length: 30 }, (_, i) => ARTIFACT({ id: `a${i}`, name: `file-${i}.json` }));
    const fetchSpy = mockFetchImpl(async (url) => {
      if (url.includes('/artifacts'))
        return new Response(JSON.stringify({ items, total: 45 }), { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await waitFor(() => expect(screen.getAllByTestId('artifact-card').length).toBe(30));
    expect(screen.getByRole('navigation', { name: /pagination/i })).toBeInTheDocument();

    const callsBefore = fetchSpy.mock.calls.length;
    await userEvent.click(screen.getByRole('button', { name: /next page/i }));
    await waitFor(() => expect(fetchSpy.mock.calls.length).toBeGreaterThan(callsBefore));
    const lastCallUrl = String(fetchSpy.mock.calls[fetchSpy.mock.calls.length - 1][0]);
    expect(lastCallUrl).toContain('offset=30');
  });
});
