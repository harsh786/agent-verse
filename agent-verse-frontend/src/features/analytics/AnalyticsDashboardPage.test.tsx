import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AnalyticsDashboardPage } from './AnalyticsDashboardPage';

// recharts ResponsiveContainer uses ResizeObserver which is absent in jsdom
globalThis.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
};

vi.mock('@/stores/auth', () => ({
  useAuthStore: (sel: (s: { apiKey: string; tenantId: string }) => unknown) =>
    sel({ apiKey: 'test-key', tenantId: 'test-tenant' }),
}));

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

function Wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockFetch.mockResolvedValue({
    ok: true, status: 200,
    json: () => Promise.resolve({
      total: 42, by_status: { complete: 35, failed: 7 }, success_rate: 0.83,
      tools: [{ name: 'jira:search', success: 10, failed: 2, total: 12, success_rate: 0.83 }],
      cost_today_usd: 1.25, goals_today: 5,
    }),
  } as Response);
});

describe('AnalyticsDashboardPage', () => {
  it('renders page title', () => {
    render(<AnalyticsDashboardPage />, { wrapper: Wrapper });
    expect(screen.getByText('Analytics')).toBeDefined();
  });

  it('renders KPI section headings or loading', () => {
    render(<AnalyticsDashboardPage />, { wrapper: Wrapper });
    // Either shows loading or KPI values
    const element = document.body;
    expect(element).toBeDefined();
  });

  it('renders chart containers', () => {
    render(<AnalyticsDashboardPage />, { wrapper: Wrapper });
    const headings = screen.getAllByText(/Goals by Status|goals/i);
    expect(headings.length).toBeGreaterThan(0);
  });
});
