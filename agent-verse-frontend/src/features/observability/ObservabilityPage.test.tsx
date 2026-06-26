import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ObservabilityPage } from './ObservabilityPage';

function renderObservabilityPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ObservabilityPage />
    </QueryClientProvider>
  );
}

/** Returns a fetch mock implementation that handles all ObservabilityPage requests */
function makeObservabilityFetch({
  healthData,
  metricsText = '# metrics\nsome_metric 1.0',
  grafanaOk = true,
  failHealth = false,
}: {
  healthData?: object;
  metricsText?: string;
  grafanaOk?: boolean;
  failHealth?: boolean;
}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('localhost:3001')) {
      if (grafanaOk) return new Response(null, { status: 200 });
      throw new Error('Connection refused');
    }
    if (url.endsWith('/health')) {
      if (failHealth) {
        return new Response(null, { status: 503, statusText: 'Service Unavailable' });
      }
      return new Response(
        JSON.stringify(healthData ?? { status: 'ok', version: '1.0.0' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }
    if (url.endsWith('/metrics')) {
      return new Response(metricsText, {
        status: 200,
        headers: { 'Content-Type': 'text/plain' },
      });
    }
    return new Response(null, { status: 404 });
  });
}

describe('ObservabilityPage', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'free',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders page title', () => {
    makeObservabilityFetch({});
    renderObservabilityPage();
    expect(screen.getByText('Observability')).toBeInTheDocument();
  });

  test('renders Grafana Dashboard section with link', () => {
    makeObservabilityFetch({});
    renderObservabilityPage();
    expect(screen.getByText('Grafana Dashboard')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /open/i })).toBeInTheDocument();
  });

  test('shows System Health section header', () => {
    makeObservabilityFetch({});
    renderObservabilityPage();
    expect(screen.getByText('System Health')).toBeInTheDocument();
  });

  test('shows health status from API', async () => {
    makeObservabilityFetch({ healthData: { status: 'ok', version: '2.1.0' } });
    renderObservabilityPage();
    await waitFor(() => expect(screen.getByText('ok')).toBeInTheDocument());
  });

  test('shows dependencies grid when health returns dependencies', async () => {
    makeObservabilityFetch({
      healthData: {
        status: 'ok',
        dependencies: {
          postgres: { status: 'ok', latency_ms: 5 },
          redis: { status: 'ok', latency_ms: 2 },
        },
      },
    });

    renderObservabilityPage();
    await waitFor(() => expect(screen.getByText('postgres')).toBeInTheDocument());
    expect(screen.getByText('redis')).toBeInTheDocument();
    expect(screen.getByText('5ms')).toBeInTheDocument();
  });

  test('shows error when health endpoint fails', async () => {
    makeObservabilityFetch({ failHealth: true });
    renderObservabilityPage();
    await waitFor(() =>
      expect(screen.getByText(/failed to reach health endpoint/i)).toBeInTheDocument()
    );
  });

  test('shows Prometheus metrics text in metrics section', async () => {
    makeObservabilityFetch({
      healthData: { status: 'ok' },
      metricsText: '# HELP http_requests_total Total requests\nhttp_requests_total 42',
    });

    renderObservabilityPage();
    await waitFor(() =>
      expect(screen.getByText('Prometheus Metrics')).toBeInTheDocument()
    );
    await waitFor(() =>
      expect(screen.getByText(/http_requests_total/)).toBeInTheDocument()
    );
  });

  test('shows version number when returned by health API', async () => {
    makeObservabilityFetch({ healthData: { status: 'ok', version: '3.5.1' } });
    renderObservabilityPage();
    await waitFor(() => expect(screen.getByText(/v3\.5\.1/)).toBeInTheDocument());
  });
});
