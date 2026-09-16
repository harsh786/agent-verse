import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { MissionControlLayout } from './MissionControlLayout';

const mockApiFetch = vi.fn();
vi.mock('@/lib/api/client', () => ({
  apiFetch: (...args: unknown[]) => mockApiFetch(...args),
}));

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => mockNavigate };
});

function responseFor(path: string) {
  if (path.startsWith('/health')) return { status: 'healthy' };
  if (path.startsWith('/goals')) return { goals: [{ status: 'executing' }, { status: 'completed' }] };
  if (path.startsWith('/ai-ops/alerts')) return { alerts: [{ severity: 'critical' }] };
  if (path.startsWith('/ai-ops/regression-status')) return { status: 'ok' };
  return {};
}

function renderLayout(children = <div>layout-children</div>, rightPanel?: React.ReactNode, showOperationalBar?: boolean) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <MissionControlLayout rightPanel={rightPanel} showOperationalBar={showOperationalBar}>
          {children}
        </MissionControlLayout>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockNavigate.mockReset();
  mockApiFetch.mockReset();
  mockApiFetch.mockImplementation((path: string) => Promise.resolve(responseFor(path)));
});
afterEach(() => vi.restoreAllMocks());

describe('MissionControlLayout', () => {
  test('renders children in the main area', () => {
    renderLayout(<div>hello mission control</div>);
    expect(screen.getByText('hello mission control')).toBeInTheDocument();
  });

  test('renders the operational status bar by default', async () => {
    renderLayout();
    await waitFor(() => expect(screen.getByText('Operational')).toBeInTheDocument());
  });

  test('hides the operational status bar when showOperationalBar is false', () => {
    renderLayout(<div>content</div>, undefined, false);
    expect(screen.queryByText('Operational')).not.toBeInTheDocument();
    expect(screen.queryByText('Degraded')).not.toBeInTheDocument();
  });

  test('shows "Degraded" when health status is not healthy/ok', async () => {
    mockApiFetch.mockImplementation((path: string) => {
      if (path.startsWith('/health')) return Promise.resolve({ status: 'down' });
      return Promise.resolve(responseFor(path));
    });
    renderLayout();
    await waitFor(() => expect(screen.getByText('Degraded')).toBeInTheDocument());
  });

  test('shows the count of active (executing/planning) goals and navigates on click', async () => {
    mockApiFetch.mockImplementation((path: string) => {
      if (path.startsWith('/goals'))
        return Promise.resolve({
          goals: [{ status: 'executing' }, { status: 'planning' }, { status: 'completed' }],
        });
      return Promise.resolve(responseFor(path));
    });
    renderLayout();
    await waitFor(() => expect(screen.getByText('2')).toBeInTheDocument());

    fireEvent.click(screen.getByText('active').closest('button')!);
    expect(mockNavigate).toHaveBeenCalledWith('/goals?status=executing');
  });

  test('shows critical alert count and navigates to ai-ops on click', async () => {
    renderLayout();
    await waitFor(() => expect(screen.getByText('critical')).toBeInTheDocument());
    const criticalButton = screen.getByText('critical').closest('button')!;
    expect(criticalButton).toHaveTextContent('1');

    fireEvent.click(criticalButton);
    expect(mockNavigate).toHaveBeenCalledWith('/ai-ops');
  });

  test('hides the critical-alerts indicator when there are no critical alerts', async () => {
    mockApiFetch.mockImplementation((path: string) => {
      if (path.startsWith('/ai-ops/alerts')) return Promise.resolve({ alerts: [] });
      return Promise.resolve(responseFor(path));
    });
    renderLayout();
    await waitFor(() => expect(screen.getByText('Operational')).toBeInTheDocument());
    expect(screen.queryByText('critical')).not.toBeInTheDocument();
  });

  test('shows a regression indicator when regression status is not "ok"', async () => {
    mockApiFetch.mockImplementation((path: string) => {
      if (path.startsWith('/ai-ops/regression-status')) return Promise.resolve({ status: 'detected' });
      return Promise.resolve(responseFor(path));
    });
    renderLayout();
    await waitFor(() => expect(screen.getByText('Regression detected')).toBeInTheDocument());
  });

  test('does not render a right panel aside when none is given', () => {
    renderLayout(<div>content</div>);
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument();
  });

  test('renders the right panel when provided', () => {
    renderLayout(<div>content</div>, <div>inspector content</div>);
    expect(screen.getByText('inspector content')).toBeInTheDocument();
  });

  test('falls back to a default/unknown status when the health query fails', async () => {
    mockApiFetch.mockImplementation((path: string) => {
      if (path.startsWith('/health')) return Promise.reject(new Error('boom'));
      return Promise.resolve(responseFor(path));
    });
    renderLayout();
    // apiFetch calls in the component already .catch() -> {status:'unknown'}, so
    // the reject above simulates a caller whose fallback never triggers if apiFetch
    // itself resolves; verify the layout still renders without throwing.
    await waitFor(() => expect(screen.getByText(/Operational|Degraded/)).toBeInTheDocument());
  });
});
