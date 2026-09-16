import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ArtifactGallery } from './ArtifactGallery';

const ARTIFACTS = [
  { id: 'a1', title: 'Q3 Market Report', kind: 'report', status: 'approved', version: 2, quality_score: 0.9, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-03T00:00:00Z' },
  { id: 'a2', title: 'Auth module code', kind: 'code', status: 'draft', version: 1, created_at: '2026-01-02T00:00:00Z', updated_at: '2026-01-02T00:00:00Z' },
];

function mockArtifacts(list: unknown[] = ARTIFACTS) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/artifacts'))
      return new Response(JSON.stringify({ data: list }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderGallery(props: { orgId: string; missionId?: string } = { orgId: 'org-1' }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><ArtifactGallery {...props} /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ArtifactGallery', () => {
  test('renders artifact cards and the total count', async () => {
    mockArtifacts();
    renderGallery();
    expect(await screen.findByText('Q3 Market Report')).toBeInTheDocument();
    expect(screen.getByText('Auth module code')).toBeInTheDocument();
    expect(screen.getByText('2 artifacts')).toBeInTheDocument();
  });

  test('shows the empty state when the org has no artifacts', async () => {
    mockArtifacts([]);
    renderGallery();
    expect(await screen.findByText('No artifacts yet')).toBeInTheDocument();
  });

  test('the search box filters cards by title client-side', async () => {
    mockArtifacts();
    renderGallery();
    await screen.findByText('Q3 Market Report');
    await userEvent.type(screen.getByLabelText('Search artifacts'), 'auth');
    await waitFor(() => expect(screen.queryByText('Q3 Market Report')).not.toBeInTheDocument());
    expect(screen.getByText('Auth module code')).toBeInTheDocument();
    expect(screen.getByText('1 artifact')).toBeInTheDocument();
  });

  test('the kind filter narrows the list to matching artifacts', async () => {
    mockArtifacts();
    renderGallery();
    await screen.findByText('Q3 Market Report');
    await userEvent.selectOptions(screen.getByLabelText('Filter by kind'), 'code');
    await waitFor(() => expect(screen.queryByText('Q3 Market Report')).not.toBeInTheDocument());
    expect(screen.getByText('Auth module code')).toBeInTheDocument();
  });

  test('appends mission_id to the request when a missionId is provided', async () => {
    const spy = mockArtifacts();
    renderGallery({ orgId: 'org-1', missionId: 'm-99' });
    await screen.findByText('Q3 Market Report');
    expect(spy.mock.calls.some(([u]) => String(u).includes('mission_id=m-99'))).toBe(true);
  });
});
