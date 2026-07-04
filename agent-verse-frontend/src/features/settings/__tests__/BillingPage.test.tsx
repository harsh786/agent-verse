import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import BillingPage from '../BillingPage';

// Mock the auth store to provide a stable apiKey
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (s: { apiKey: string }) => string) =>
    selector({ apiKey: '' }),
}));

// Suppress fetch errors from queries with empty apiKey
vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 401 })));

afterEach(() => {
  vi.clearAllMocks();
});

function makeQc() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe('BillingPage', () => {
  it('renders without crashing', () => {
    render(
      <QueryClientProvider client={makeQc()}>
        <BillingPage />
      </QueryClientProvider>
    );
    expect(screen.getByText('Billing & Usage')).toBeTruthy();
  });

  it('shows upgrade button for non-enterprise plans', () => {
    render(
      <QueryClientProvider client={makeQc()}>
        <BillingPage />
      </QueryClientProvider>
    );
    // Upgrade button visible for free plan (default)
    expect(screen.queryByText('Upgrade Plan')).toBeTruthy();
  });
});
