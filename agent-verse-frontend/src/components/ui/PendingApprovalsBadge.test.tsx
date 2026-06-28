import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { PendingApprovalsBadge } from './PendingApprovalsBadge';

vi.mock('@/lib/sse/useEventStream', () => ({
  useEventStream: () => ({ events: [], connected: true }),
}));

function renderBadge() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><PendingApprovalsBadge /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});

test('shows the pending count', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify([
      { request_id: 'r1', goal_id: 'g1', status: 'pending' },
      { request_id: 'r2', goal_id: 'g2', status: 'pending' },
      { request_id: 'r3', goal_id: 'g3', status: 'approved' },
    ]), { status: 200, headers: { 'Content-Type': 'application/json' } }),
  );
  renderBadge();
  expect(await screen.findByText('2')).toBeInTheDocument();
});

test('renders nothing when there are no pending approvals', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } }),
  );
  const { container } = renderBadge();
  await new Promise((r) => setTimeout(r, 50));
  expect(container.querySelector('[aria-label="Pending approvals"]')).toBeNull();
});
