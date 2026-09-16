import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ObservabilityPage } from './ObservabilityPage';

// recharts uses ResizeObserver — polyfill for jsdom
(globalThis as Record<string, unknown>).ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
};

// Mock recharts to avoid SVG/canvas issues in jsdom. Axis/Tooltip mocks also
// invoke any tickFormatter/labelFormatter/formatter callback props so those
// inline formatter functions (defined in ObservabilityPage) get exercised —
// the real recharts would call them while rendering; a no-op stub would not.
vi.mock('recharts', () => ({
  BarChart: ({ children }: any) => <div data-testid="bar-chart">{children}</div>,
  Bar: () => null,
  AreaChart: ({ children }: any) => <div data-testid="area-chart">{children}</div>,
  Area: () => null,
  LineChart: ({ children }: any) => <div data-testid="line-chart">{children}</div>,
  Line: () => null,
  XAxis: (props: any) => {
    props.tickFormatter?.('2026-01-01T12:34:00Z');
    return null;
  },
  YAxis: (props: any) => {
    props.tickFormatter?.(1.2345);
    return null;
  },
  CartesianGrid: () => null,
  Tooltip: (props: any) => {
    props.labelFormatter?.('2026-01-01T12:34:00Z');
    props.formatter?.(1.2345);
    return null;
  },
  Legend: () => null,
  ResponsiveContainer: ({ children }: any) => <div>{children}</div>,
}));

// Mock EventSource globally (jsdom has no native implementation).
class MockEventSource {
  url: string;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(url: string) {
    this.url = url;
  }
  close() {
    this.closed = true;
  }
  emit(data: object) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(data) }));
  }
  fail() {
    this.onerror?.();
  }
}
let mockEsInstances: MockEventSource[] = [];
vi.stubGlobal(
  'EventSource',
  class {
    constructor(url: string) {
      const inst = new MockEventSource(url);
      mockEsInstances.push(inst);
      return inst;
    }
  } as unknown as typeof EventSource,
);

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ObservabilityPage />
    </QueryClientProvider>
  );
}

interface FetchOpts {
  healthData?: object;
  metricsText?: string;
  grafanaOk?: boolean;
  failHealth?: boolean;
  spans?: object[];
  structuredMetrics?: object;
  timeseries?: object;
  logs?: object[];
}

function makeFetch({
  healthData,
  metricsText = '# metrics',
  grafanaOk = false,
  failHealth = false,
  spans = [] as object[],
  structuredMetrics,
  timeseries,
  logs = [] as object[],
}: FetchOpts = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('localhost:3001')) {
      if (grafanaOk) return new Response(null, { status: 200 });
      throw new Error('Connection refused');
    }
    if (url.endsWith('/health')) {
      if (failHealth)
        return new Response(null, { status: 503, statusText: 'Service Unavailable' });
      return new Response(
        JSON.stringify(healthData ?? { status: 'healthy', version: '1.0.0' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }
    if (url.endsWith('/metrics')) {
      return new Response(metricsText, { status: 200, headers: { 'Content-Type': 'text/plain' } });
    }
    if (url.includes('/observability/metrics')) {
      return new Response(JSON.stringify(structuredMetrics ?? {}), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/observability/timeseries')) {
      return new Response(
        JSON.stringify(timeseries ?? { goals_per_hour: [], cost_per_hour: [], avg_latency_per_hour: [] }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }
    if (url.includes('/observability/logs') && !url.includes('stream')) {
      return new Response(JSON.stringify({ logs, total: logs.length }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/analytics/observability/spans')) {
      return new Response(JSON.stringify(spans), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

describe('ObservabilityPage — additional branches', () => {
  beforeEach(() => {
    mockEsInstances = [];
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'free',
      isAuthenticated: true,
    });
  });
  afterEach(() => vi.restoreAllMocks());

  // ── Overview tab branches ───────────────────────────────────────────────────

  test('shows degraded status styling and icon', async () => {
    makeFetch({
      healthData: {
        status: 'degraded',
        checks: { cache: { status: 'degraded', message: 'slow' } },
      },
    });
    renderPage();
    await waitFor(() => expect(screen.getAllByText('degraded').length).toBeGreaterThan(0));
    expect(screen.getByText('slow')).toBeInTheDocument();
  });

  test('shows a red/unhealthy dependency row for an unrecognized status', async () => {
    makeFetch({
      healthData: {
        status: 'down',
        checks: { queue: { status: 'down', error: 'timeout' } },
      },
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('queue')).toBeInTheDocument());
    expect(screen.getByText('timeout')).toBeInTheDocument();
  });

  test('shows the empty-state message when health has no dependency checks', async () => {
    makeFetch({ healthData: { status: 'healthy' } });
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/No dependency checks registered/i)).toBeInTheDocument()
    );
  });

  test('renders Platform Architecture section', () => {
    makeFetch();
    renderPage();
    expect(screen.getByText('Platform Architecture')).toBeInTheDocument();
    expect(screen.getByText('API Gateway')).toBeInTheDocument();
  });

  // ── Time range picker branches ──────────────────────────────────────────────

  test('switching between preset time ranges works', async () => {
    makeFetch();
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: '6h' }));
    await userEvent.click(screen.getByRole('button', { name: '7d' }));
    await userEvent.click(screen.getByRole('button', { name: '30d' }));
    await userEvent.click(screen.getByRole('button', { name: '1h' }));
    // no crash; time range picker still visible
    expect(screen.getByText('Time range:')).toBeInTheDocument();
  });

  test('custom time range: opens the picker, Apply is disabled until both dates set, then applies', async () => {
    makeFetch();
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: 'Custom' }));
    const applyBtn = screen.getByRole('button', { name: 'Apply' });
    expect(applyBtn).toBeDisabled();

    const inputs = document.querySelectorAll('input[type="datetime-local"]');
    expect(inputs.length).toBe(2);
    fireEvent.change(inputs[0], { target: { value: '2026-01-01T00:00' } });
    expect(applyBtn).toBeDisabled();
    fireEvent.change(inputs[1], { target: { value: '2026-01-02T00:00' } });
    expect(applyBtn).not.toBeDisabled();
    await userEvent.click(applyBtn);
    // Custom picker closes back down after apply.
    await waitFor(() =>
      expect(document.querySelectorAll('input[type="datetime-local"]').length).toBe(0)
    );
  });

  test('toggling Custom closed again hides the custom date inputs', async () => {
    makeFetch();
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: 'Custom' }));
    expect(document.querySelectorAll('input[type="datetime-local"]').length).toBe(2);
    await userEvent.click(screen.getByRole('button', { name: 'Custom' }));
    expect(document.querySelectorAll('input[type="datetime-local"]').length).toBe(0);
  });

  // ── Header controls ──────────────────────────────────────────────────────────

  test('manual refresh button invalidates observability queries', async () => {
    makeFetch();
    renderPage();
    const refreshButtons = screen.getAllByTitle('Refresh now');
    await userEvent.click(refreshButtons[0]);
    // Still renders fine post refresh
    expect(screen.getByText('Observability')).toBeInTheDocument();
  });

  test('toggling auto-refresh flips the Manual/Auto label', async () => {
    makeFetch();
    renderPage();
    const toggle = screen.getByText('Manual');
    await userEvent.click(toggle);
    expect(screen.getByText('Auto')).toBeInTheDocument();
    await userEvent.click(screen.getByText('Auto'));
    expect(screen.getByText('Manual')).toBeInTheDocument();
  });

  test('auto-refresh interval fires and invalidates observability queries', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      makeFetch();
      renderPage();
      await vi.waitFor(() => expect(screen.getByText('Manual')).toBeInTheDocument());
      fireEvent.click(screen.getByText('Manual'));
      await vi.waitFor(() => expect(screen.getByText('Auto')).toBeInTheDocument());
      // 24h range refresh interval is 60s.
      await vi.advanceTimersByTimeAsync(60_000);
      expect(screen.getByText('Auto')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  test('Grafana link is enabled/styled when Grafana is reachable', async () => {
    makeFetch({ grafanaOk: true });
    renderPage();
    await waitFor(() => {
      const link = screen.getByText('Grafana').closest('a')!;
      expect(link).not.toHaveClass('pointer-events-none');
    });
  });

  test('Grafana link is disabled when Grafana is unreachable', async () => {
    makeFetch({ grafanaOk: false });
    renderPage();
    await waitFor(() => {
      const link = screen.getByText('Grafana').closest('a')!;
      expect(link).toHaveClass('pointer-events-none');
    });
  });

  // ── Metrics tab branches ────────────────────────────────────────────────────

  test('metrics tab parses success rate + queue depth + tool/token label data from prometheus text', async () => {
    const metricsText = [
      '# HELP agentverse_goal_success_total',
      'agentverse_goal_success_total 0.87',
      'agentverse_queue_depth 4',
      'agentverse_tool_call_total{tool="github.create_issue"} 12',
      'agentverse_tool_call_total{tool="slack.post"} 7',
      'agentverse_llm_tokens_total{provider="anthropic"} 5000',
    ].join('\n');
    makeFetch({ metricsText });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Metrics' }));
    await waitFor(() => expect(screen.getByText('87.0%')).toBeInTheDocument());
    expect(screen.getByText('4')).toBeInTheDocument();
  });

  test('metrics tab shows structured latency percentiles from array shape', async () => {
    makeFetch({
      structuredMetrics: {
        goal_duration_percentiles: [
          { percentile: 'p50', ms: 120 },
          { percentile: 'p95', ms: 400 },
          { percentile: 'p99', ms: 900 },
        ],
      },
    });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Metrics' }));
    await waitFor(() => expect(screen.getByText('120ms')).toBeInTheDocument());
    expect(screen.getByText('900ms')).toBeInTheDocument();
  });

  test('metrics tab shows structured latency percentiles from object shape', async () => {
    makeFetch({
      structuredMetrics: { latency_percentiles: { p50: 0.2, p95: 0.5, p99: 1.1 } },
    });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Metrics' }));
    await waitFor(() => expect(screen.getByText('200ms')).toBeInTheDocument());
    expect(screen.getByText('1100ms')).toBeInTheDocument();
  });

  test('metrics tab renders time-series charts when data is present', async () => {
    makeFetch({
      timeseries: {
        goals_per_hour: [{ ts: '2026-01-01T00:00:00Z', count: 5, success: 4, failed: 1 }],
        cost_per_hour: [{ ts: '2026-01-01T00:00:00Z', cost_usd: 0.02 }],
        avg_latency_per_hour: [{ ts: '2026-01-01T00:00:00Z', p50_ms: 100, p95_ms: 300 }],
      },
    });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Metrics' }));
    await waitFor(() => expect(screen.getByText('Goal Throughput')).toBeInTheDocument());
    expect(screen.getByText('Cost Over Time')).toBeInTheDocument();
    expect(screen.getByText('Latency Trend')).toBeInTheDocument();
  });

  test('metrics tab shows the "no activity" empty state when time series is empty', async () => {
    makeFetch({ timeseries: { goals_per_hour: [], cost_per_hour: [], avg_latency_per_hour: [] } });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Metrics' }));
    await waitFor(() =>
      expect(screen.getByText(/No activity in the selected time range/i)).toBeInTheDocument()
    );
  });

  test('metrics tab refresh button refetches raw metrics', async () => {
    makeFetch({ metricsText: 'http_requests_total 1' });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Metrics' }));
    // Two "Refresh"-named buttons exist (header icon-only + metrics-tab text
    // button) — the metrics one renders the visible "Refresh" text label.
    const refreshBtn = await screen.findByText('Refresh');
    await userEvent.click(refreshBtn.closest('button')!);
    expect(screen.getByText('Refresh')).toBeInTheDocument();
  });

  test('metrics tab shows token spend empty state when no token data', async () => {
    makeFetch({ metricsText: '# empty' });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Metrics' }));
    await waitFor(() =>
      expect(screen.getByText(/No token usage data available yet/i)).toBeInTheDocument()
    );
  });

  test('metrics tab shows tool-call empty state copy when no tool data', async () => {
    makeFetch({ metricsText: '# empty' });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Metrics' }));
    await waitFor(() =>
      expect(screen.getByText(/No tool call data yet/i)).toBeInTheDocument()
    );
  });

  // ── Traces tab branches ─────────────────────────────────────────────────────

  const now = Date.now() * 1e6;
  const spanA = {
    name: 'agent.execute',
    trace_id: 'trace-a',
    span_id: 'sp-a',
    start_time: now,
    end_time: now + 5e7,
    attributes: { 'goal.id': 'g-1' },
    status: 'OK',
  };
  const spanB = {
    name: 'tool.call',
    trace_id: 'trace-a',
    span_id: 'sp-b',
    start_time: now + 1e7,
    end_time: now + 6e7,
    attributes: {},
    status: 'ERROR',
    parent_span_id: 'sp-a',
  };

  test('traces tab filters by search text', async () => {
    makeFetch({ spans: [spanA, spanB] });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Traces' }));
    await waitFor(() => expect(screen.getByText('agent.execute')).toBeInTheDocument());
    await userEvent.type(screen.getByPlaceholderText('Search spans…'), 'tool');
    expect(screen.queryByText('agent.execute')).not.toBeInTheDocument();
    expect(screen.getByText('tool.call')).toBeInTheDocument();
  });

  test('traces tab filters by status', async () => {
    makeFetch({ spans: [spanA, spanB] });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Traces' }));
    await waitFor(() => expect(screen.getByText('agent.execute')).toBeInTheDocument());
    const selects = screen.getAllByRole('combobox');
    const statusSelect = selects.find((s) => s.innerHTML.includes('All statuses'))!;
    fireEvent.change(statusSelect, { target: { value: 'ERROR' } });
    expect(screen.queryByText('agent.execute')).not.toBeInTheDocument();
    expect(screen.getByText('tool.call')).toBeInTheDocument();
  });

  test('selecting a span shows its detail panel with attributes, and × clears it', async () => {
    makeFetch({ spans: [spanA] });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Traces' }));
    await waitFor(() => expect(screen.getByText('agent.execute')).toBeInTheDocument());
    await userEvent.click(screen.getByText('agent.execute'));
    expect(screen.getByText('Attributes')).toBeInTheDocument();
    expect(screen.getByText('goal.id')).toBeInTheDocument();
    await userEvent.click(screen.getByText('×'));
    expect(screen.queryByText('Attributes')).not.toBeInTheDocument();
  });

  // ── Logs tab branches ────────────────────────────────────────────────────────

  test('logs tab filters by level and by search text, and can clear filters', async () => {
    makeFetch({
      logs: [
        { id: '1', timestamp: new Date().toISOString(), level: 'info', message: 'hello world', source: 'agent' },
        { id: '2', timestamp: new Date().toISOString(), level: 'error', message: 'boom', source: 'executor' },
      ],
    });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Logs' }));
    await waitFor(() => expect(screen.getByText('hello world')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'ERROR' }));
    expect(screen.queryByText('hello world')).not.toBeInTheDocument();
    expect(screen.getByText('boom')).toBeInTheDocument();

    await userEvent.click(screen.getByText('(clear filters)'));
    await waitFor(() => expect(screen.getByText('hello world')).toBeInTheDocument());

    await userEvent.type(screen.getByPlaceholderText('Search logs…'), 'boom');
    expect(screen.queryByText('hello world')).not.toBeInTheDocument();
    expect(screen.getByText('boom')).toBeInTheDocument();
  });

  test('logs tab shows empty state when there are no logs at all', async () => {
    makeFetch({ logs: [] });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Logs' }));
    await waitFor(() =>
      expect(screen.getByText(/No logs yet — execute a goal/i)).toBeInTheDocument()
    );
  });

  test('logs tab shows "no entries match" when filters exclude everything', async () => {
    makeFetch({
      logs: [{ id: '1', timestamp: new Date().toISOString(), level: 'info', message: 'hi' }],
    });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Logs' }));
    await waitFor(() => expect(screen.getByText('hi')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'DEBUG' }));
    expect(screen.getByText(/No entries match the current filters/i)).toBeInTheDocument();
  });

  test('logs tab receives a live SSE log entry and prepends it', async () => {
    makeFetch({ logs: [] });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Logs' }));
    await waitFor(() => expect(mockEsInstances.length).toBeGreaterThan(0));
    const es = mockEsInstances[mockEsInstances.length - 1];
    es.emit({ id: 'live-1', timestamp: new Date().toISOString(), level: 'warning', message: 'live entry', source: 'sse' });
    await waitFor(() => expect(screen.getByText('live entry')).toBeInTheDocument());
  });

  test('logs tab pauses the live stream on hover and resumes on mouse leave', async () => {
    makeFetch({
      logs: [{ id: '1', timestamp: new Date().toISOString(), level: 'info', message: 'hi' }],
    });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Logs' }));
    await waitFor(() => expect(screen.getByText('hi')).toBeInTheDocument());
    expect(screen.getByText('Live')).toBeInTheDocument();
    const logPane = screen.getByText('hi').closest('div')!.parentElement!;
    fireEvent.mouseEnter(logPane);
    expect(screen.getByText(/Paused/)).toBeInTheDocument();
    fireEvent.mouseLeave(logPane);
    await waitFor(() => expect(screen.getByText('Live')).toBeInTheDocument());
  });

  test('logs tab export button downloads a text blob when logs exist', async () => {
    makeFetch({
      logs: [{ id: '1', timestamp: new Date().toISOString(), level: 'info', message: 'exportable' }],
    });
    const createObjectURL = vi.fn(() => 'blob:mock');
    const revokeObjectURL = vi.fn();
    (globalThis.URL as unknown as { createObjectURL: unknown }).createObjectURL = createObjectURL;
    (globalThis.URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = revokeObjectURL;
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Logs' }));
    await waitFor(() => expect(screen.getByText('exportable')).toBeInTheDocument());
    const exportBtn = screen.getByRole('button', { name: /Export Logs/i });
    expect(exportBtn).not.toBeDisabled();
    await userEvent.click(exportBtn);
    expect(createObjectURL).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock');
  });

  test('logs tab SSE stream closes the EventSource on error', async () => {
    makeFetch({ logs: [] });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Logs' }));
    await waitFor(() => expect(mockEsInstances.length).toBeGreaterThan(0));
    const es = mockEsInstances[mockEsInstances.length - 1];
    expect(es.closed).toBe(false);
    es.fail();
    expect(es.closed).toBe(true);
  });

  test('logs tab export button is disabled when there are no logs', async () => {
    makeFetch({ logs: [] });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Logs' }));
    await waitFor(() => expect(screen.getByRole('button', { name: /Export Logs/i })).toBeDisabled());
  });
});
