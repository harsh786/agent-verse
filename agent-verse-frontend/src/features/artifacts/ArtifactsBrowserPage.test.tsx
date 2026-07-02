import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
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
    expect(screen.queryByText('report.json')).not.toBeInTheDocument();
    expect(screen.getByText('screenshot.png')).toBeInTheDocument();
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
});
