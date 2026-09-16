import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ArtifactGallery } from './ArtifactGallery';

const ARTIFACTS = [
  { id: 'a1', name: 'report.pdf', type: 'report', size_bytes: 2048, created_at: '2026-01-01T00:00:00Z', url: 'https://x/report.pdf' },
  { id: 'a2', name: 'diagram.png', type: 'image', size_bytes: 512, created_at: '2026-01-02T00:00:00Z' },
  { id: 'a3', name: 'script.py', type: 'code', size_bytes: 4096, created_at: '2026-01-03T00:00:00Z' },
];

function mockFetch(artifacts: unknown[] = ARTIFACTS) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/v1/artifacts'))
      return new Response(JSON.stringify(artifacts), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderGallery(props = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ArtifactGallery goalId="g1" {...props} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ArtifactGallery', () => {
  test('renders artifacts with formatted sizes', async () => {
    mockFetch();
    renderGallery();
    expect(await screen.findByText('report.pdf')).toBeInTheDocument();
    expect(screen.getByText('diagram.png')).toBeInTheDocument();
    expect(screen.getByText('script.py')).toBeInTheDocument();
    // 2048 bytes → 2.0KB, 512 bytes → 512B
    expect(screen.getByText('2.0KB')).toBeInTheDocument();
    expect(screen.getByText('512B')).toBeInTheDocument();
  });

  test('scopes the request by goal_id query param', async () => {
    const spy = mockFetch();
    renderGallery({ goalId: 'goal-xyz' });
    await screen.findByText('report.pdf');
    expect(spy.mock.calls.some(([u]) => String(u).includes('goal_id=goal-xyz'))).toBe(true);
  });

  test('search filters the visible artifacts by name', async () => {
    mockFetch();
    renderGallery();
    await screen.findByText('report.pdf');
    await userEvent.type(screen.getByLabelText('Search artifacts'), 'diagram');
    expect(screen.getByText('diagram.png')).toBeInTheDocument();
    expect(screen.queryByText('report.pdf')).not.toBeInTheDocument();
    expect(screen.queryByText('script.py')).not.toBeInTheDocument();
  });

  test('type filter narrows results to the chosen type', async () => {
    mockFetch();
    renderGallery();
    await screen.findByText('report.pdf');
    await userEvent.selectOptions(screen.getByLabelText('Filter by type'), 'code');
    expect(screen.getByText('script.py')).toBeInTheDocument();
    expect(screen.queryByText('report.pdf')).not.toBeInTheDocument();
  });

  test('switching to list view sets aria-pressed on the list toggle', async () => {
    mockFetch();
    renderGallery();
    await screen.findByText('report.pdf');
    const listBtn = screen.getByRole('button', { name: 'list view' });
    expect(listBtn).toHaveAttribute('aria-pressed', 'false');
    await userEvent.click(listBtn);
    expect(listBtn).toHaveAttribute('aria-pressed', 'true');
    // Content still shown after the layout switch.
    expect(screen.getByText('report.pdf')).toBeInTheDocument();
  });

  test('shows the empty state when there are no artifacts', async () => {
    mockFetch([]);
    renderGallery();
    expect(await screen.findByText('No artifacts yet')).toBeInTheDocument();
    expect(screen.getByText(/Artifacts appear after goal execution/i)).toBeInTheDocument();
  });

  test('search with no match shows the "No matches found" hint', async () => {
    mockFetch();
    renderGallery();
    await screen.findByText('report.pdf');
    await userEvent.type(screen.getByLabelText('Search artifacts'), 'zzzznope');
    await waitFor(() => expect(screen.getByText('No matches found')).toBeInTheDocument());
  });
});
