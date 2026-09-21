import { render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StatusPage } from './StatusPage';

function renderStatusPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><StatusPage /></QueryClientProvider>);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('StatusPage', () => {
  it('renders the status page heading', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'operational',
        components: {
          postgres: { status: 'operational', latency_ms: 2.4 },
          redis: { status: 'operational', latency_ms: 0.8 },
        },
        timestamp: Date.now() / 1000,
      }),
    }));
    renderStatusPage();
    expect(screen.getByText('AgentVerse Status')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('All Systems Operational')).toBeInTheDocument());
  });

  it('has a refresh button that re-triggers the fetch', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'operational',
        components: { postgres: { status: 'operational', latency_ms: 2.4 } },
        timestamp: Date.now() / 1000,
      }),
    });
    vi.stubGlobal('fetch', fetchMock);
    renderStatusPage();
    const button = screen.getByRole('button', { name: /refresh/i });
    expect(button).toBeInTheDocument();

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const user = userEvent.setup();
    await user.click(button);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it('shows a loading skeleton while the status request is in flight', async () => {
    let resolveFetch: (value: unknown) => void = () => {};
    const pending = new Promise(resolve => { resolveFetch = resolve; });
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(pending));

    renderStatusPage();

    expect(screen.getByLabelText('Loading status')).toBeInTheDocument();

    resolveFetch({
      ok: true,
      json: async () => ({
        status: 'operational',
        components: {},
        timestamp: Date.now() / 1000,
      }),
    });

    await waitFor(() => expect(screen.queryByLabelText('Loading status')).not.toBeInTheDocument());
  });

  it('shows an error message when the status endpoint is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network down')));
    renderStatusPage();

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument(), { timeout: 5000 });
    expect(screen.getByText(/Unable to load status/i)).toBeInTheDocument();
    expect(screen.queryByLabelText('Loading status')).not.toBeInTheDocument();
  });

  it('shows the degraded banner copy when a component is degraded', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'degraded',
        components: {
          postgres: { status: 'degraded', latency_ms: 812.3 },
          redis: { status: 'operational', latency_ms: 0.8 },
        },
        timestamp: Date.now() / 1000,
      }),
    }));
    renderStatusPage();

    await waitFor(() => expect(screen.getByText('Partial Service Disruption')).toBeInTheDocument());
    expect(screen.getByText('Some services are experiencing issues.')).toBeInTheDocument();

    const list = screen.getByRole('list', { name: /service components/i });
    const postgresRow = within(list).getByText('postgres').closest('li');
    expect(postgresRow).not.toBeNull();
    expect(within(postgresRow as HTMLElement).getByText('812ms')).toBeInTheDocument();
    expect(within(postgresRow as HTMLElement).getByText('degraded')).toBeInTheDocument();
  });

  it('falls back to the unknown status config for an unrecognized overall status', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'something-weird',
        components: {
          cache: { status: 'something-weird' },
        },
        timestamp: Date.now() / 1000,
      }),
    }));
    renderStatusPage();

    await waitFor(() => expect(screen.queryByLabelText('Loading status')).not.toBeInTheDocument());
    expect(screen.getByText('Status Unknown')).toBeInTheDocument();
    // Degraded-only subtext must not appear for the unknown overall status.
    expect(screen.queryByText('Some services are experiencing issues.')).not.toBeInTheDocument();

    const list = screen.getByRole('list', { name: /service components/i });
    const cacheRow = within(list).getByText('cache').closest('li');
    expect(cacheRow).not.toBeNull();
    // No latency_ms provided, so no "ms" reading should render for this component.
    expect(within(cacheRow as HTMLElement).queryByText(/ms$/)).not.toBeInTheDocument();
    expect(within(cacheRow as HTMLElement).getByText('something-weird')).toBeInTheDocument();
  });

  it('renders underscored component names as spaced, capitalized labels', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'operational',
        components: {
          vector_store: { status: 'operational', latency_ms: 5 },
        },
        timestamp: Date.now() / 1000,
      }),
    }));
    renderStatusPage();

    await waitFor(() => expect(screen.getByText('vector store')).toBeInTheDocument());
  });
});
