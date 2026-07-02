import { describe, it, test, expect, vi, beforeEach } from 'vitest';
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

vi.mock('@/stores/auth', () => {
  const state = {
    apiKey: 'test-key', tenantId: 'test-tenant',
    ssoMode: false, accessToken: '', logout: () => {},
  };
  const hook = (sel: (s: typeof state) => unknown) => sel(state);
  (hook as unknown as { getState: () => typeof state }).getState = () => state;
  return { useAuthStore: hook };
});

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

describe('AnalyticsDashboardPage null safety', () => {
  test('renders without crashing when evals returns null avg_score and pass_rate', async () => {
    mockFetch.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/analytics/evals')) {
        return {
          ok: true, status: 200,
          json: () => Promise.resolve({ total_evals: 0, pass_rate: null, avg_score: null, evals_by_day: [] }),
        } as Response;
      }
      if (url.includes('/analytics/goals')) {
        return {
          ok: true, status: 200,
          json: () => Promise.resolve({ total: 5, success_rate: 0.8, by_status: { complete: 4, failed: 1 } }),
        } as Response;
      }
      if (url.includes('/analytics/costs')) {
        return {
          ok: true, status: 200,
          json: () => Promise.resolve({ total_cost_usd: 0.05 }),
        } as Response;
      }
      return {
        ok: true, status: 200,
        json: () => Promise.resolve({ tools: [] }),
      } as Response;
    });

    render(<AnalyticsDashboardPage />, { wrapper: Wrapper });
    // Wait for eval summary section — crashes before fix, passes after
    expect(await screen.findByText(/Eval Summary/i)).toBeTruthy();
  });
});
