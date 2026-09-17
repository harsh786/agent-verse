import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { TrainingExportPage } from './TrainingExportPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <TrainingExportPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const PREVIEW = {
  count: 7,
  avg_score: 0.92,
  min_score_found: 0.81,
  max_score_found: 0.99,
  score_distribution: { '0.80-0.85': 1, '0.85-0.90': 2, '0.90-0.95': 3, '0.95-1.00': 1 },
  samples: [
    { goal: 'Triage Jira issues', eval_score: 0.97, steps: 3, tools: ['jira_search'] },
    { goal: 'Refactor payments module', eval_score: 0.91, steps: 5, tools: ['git_diff', 'lint', 'test'] },
    { goal: 'Low score sample', eval_score: 0.82, steps: 1, tools: [] },
  ],
};

function mockPreviewFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    if (String(input).includes('preview'))
      return new Response(JSON.stringify(PREVIEW), { status: 200 });
    return new Response('{}', { status: 200 });
  });
}

function exportResponse(body: string, extra?: Record<string, string>) {
  return new Response(body, {
    status: 200,
    headers: {
      'Content-Type': 'application/x-ndjson',
      'Content-Disposition': 'attachment; filename="agentverse_training_openai_x.jsonl"',
      'X-Training-Examples': '7',
      ...extra,
    },
  });
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
  useToastStore.setState({ toasts: [] });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  vi.stubGlobal('URL', {
    ...URL,
    createObjectURL: vi.fn().mockReturnValue('blob:x'),
    revokeObjectURL: vi.fn(),
  });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('TrainingExportPage', () => {
  test('renders export controls', async () => {
    mockPreviewFetch();
    renderPage();
    expect(await screen.findByLabelText(/^format$/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/minimum eval score/i)).toBeInTheDocument();
    expect(screen.getByTestId('btn-export')).toBeInTheDocument();
  });

  test('triggers export and shows the example count', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('preview'))
        return new Response(JSON.stringify(PREVIEW), { status: 200 });
      if (url.includes('/intelligence/export-training-data') && init?.method === 'POST')
        return exportResponse('{"a":1}');
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByLabelText(/^format$/i);
    await userEvent.click(screen.getByTestId('btn-export'));
    expect(await screen.findByText(/7 examples exported successfully/i)).toBeInTheDocument();
    const call = f.mock.calls.find(
      ([u, init]) => String(u).includes('/intelligence/export-training-data') && (init as RequestInit)?.method === 'POST'
    );
    expect(call).toBeTruthy();
    expect(String(call![0])).toContain('format=openai');
  });

  test('shows score distribution chart after preview loads', async () => {
    mockPreviewFetch();
    renderPage();
    expect(await screen.findByTestId('score-distribution')).toBeInTheDocument();
  });

  test('shows sample goal text after preview loads', async () => {
    mockPreviewFetch();
    renderPage();
    expect(await screen.findByText(/Triage Jira issues/i)).toBeInTheDocument();
  });

  test('shows loading state before preview resolves', async () => {
    let resolveFetch: (r: Response) => void = () => {};
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      () => new Promise((resolve) => { resolveFetch = resolve; })
    );
    renderPage();
    expect(await screen.findByText(/loading preview/i)).toBeInTheDocument();
    resolveFetch(new Response(JSON.stringify(PREVIEW), { status: 200 }));
    expect(await screen.findByTestId('score-distribution')).toBeInTheDocument();
  });

  test('refresh preview button re-triggers the query', async () => {
    const f = mockPreviewFetch();
    renderPage();
    await screen.findByTestId('score-distribution');
    const callsBefore = f.mock.calls.filter(([u]) => String(u).includes('preview')).length;
    await userEvent.click(screen.getByRole('button', { name: /refresh preview/i }));
    await screen.findByTestId('score-distribution');
    const callsAfter = f.mock.calls.filter(([u]) => String(u).includes('preview')).length;
    expect(callsAfter).toBeGreaterThan(callsBefore);
  });

  test('shows a date-range validation error when to-date precedes from-date', async () => {
    mockPreviewFetch();
    renderPage();
    await screen.findByLabelText(/^format$/i);
    const dateInputs = document.querySelectorAll('input[type="date"]');
    const [fromInput, toInput] = Array.from(dateInputs) as HTMLInputElement[];
    await userEvent.type(fromInput, '2026-05-10');
    await userEvent.type(toInput, '2026-01-01');
    expect(await screen.findByText(/end date must be after start date/i)).toBeInTheDocument();
  });

  test('changing format updates the description and sample preview rendering', async () => {
    mockPreviewFetch();
    renderPage();
    const select = await screen.findByLabelText(/^format$/i);
    await screen.findByText(/messages array with system\/user\/assistant/i);

    await userEvent.selectOptions(select, 'anthropic');
    expect(await screen.findByText(/human\/assistant message pairs/i)).toBeInTheDocument();

    await userEvent.selectOptions(select, 'llama');
    expect(await screen.findByText(/instruction \+ output format/i)).toBeInTheDocument();

    await userEvent.selectOptions(select, 'sharegpt');
    expect(await screen.findByText(/conversations array format/i)).toBeInTheDocument();
  });

  test('score badges reflect low/medium/high eval scores in sample records', async () => {
    mockPreviewFetch();
    renderPage();
    await screen.findByText(/Triage Jira issues/i);
    expect(screen.getByText('score 0.97')).toBeInTheDocument();
    expect(screen.getByText('score 0.91')).toBeInTheDocument();
    // only first 2 samples are rendered by SampleRecords (slice(0,2))
    expect(screen.queryByText('score 0.82')).not.toBeInTheDocument();
  });

  test('adjusting the max examples field clamps to a minimum of 1', async () => {
    mockPreviewFetch();
    renderPage();
    const limitInput = await screen.findByLabelText(/max examples/i);
    fireEvent.change(limitInput, { target: { value: '-5' } });
    expect(limitInput).toHaveValue(1);
  });

  test('export with zero matching examples shows an info toast and no download', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('preview'))
        return new Response(JSON.stringify({ ...PREVIEW, count: 0 }), { status: 200 });
      if (url.includes('/intelligence/export-training-data') && init?.method === 'POST')
        return exportResponse('', { 'X-Training-Examples': '0' });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByLabelText(/^format$/i);
    await userEvent.click(screen.getByTestId('btn-export'));
    await screen.findByText(/no examples matched the current filters/i);
    const toasts = useToastStore.getState().toasts;
    expect(toasts.some((t) => t.kind === 'info' && /no examples matched the filters/i.test(t.message))).toBe(true);
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  test('export failure surfaces an error toast', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('preview'))
        return new Response(JSON.stringify(PREVIEW), { status: 200 });
      if (url.includes('/intelligence/export-training-data') && init?.method === 'POST')
        return new Response('error', { status: 500, statusText: 'Server Error' });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByLabelText(/^format$/i);
    await userEvent.click(screen.getByTestId('btn-export'));
    await vi.waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'error' && /export failed/i.test(t.message))).toBe(true);
    });
  });

  test('exporting as llama converts records client-side and records history with success toast', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('preview'))
        return new Response(JSON.stringify(PREVIEW), { status: 200 });
      if (url.includes('/intelligence/export-training-data') && init?.method === 'POST') {
        const line = JSON.stringify({
          messages: [
            { role: 'user', content: 'do the thing' },
            { role: 'assistant', content: 'done' },
          ],
        });
        return exportResponse(line);
      }
      return new Response('{}', { status: 200 });
    });
    renderPage();
    const select = await screen.findByLabelText(/^format$/i);
    await userEvent.selectOptions(select, 'llama');
    await userEvent.click(screen.getByTestId('btn-export'));
    await screen.findByText(/7 examples exported successfully/i);
    const toasts = useToastStore.getState().toasts;
    expect(toasts.some((t) => t.kind === 'success' && /Llama \/ Mistral/i.test(t.message))).toBe(true);
    expect(URL.createObjectURL).toHaveBeenCalled();

    // export success auto-opens the history panel
    const historyPanel = await screen.findByText(/^Export History$/);
    const panel = historyPanel.closest('div')!.parentElement!;
    expect(within(panel as HTMLElement).getByText('llama')).toBeInTheDocument();
  });

  test('malformed export payload triggers a format-conversion error toast for sharegpt', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('preview'))
        return new Response(JSON.stringify(PREVIEW), { status: 200 });
      if (url.includes('/intelligence/export-training-data') && init?.method === 'POST')
        return exportResponse('not valid json\n');
      return new Response('{}', { status: 200 });
    });
    renderPage();
    const select = await screen.findByLabelText(/^format$/i);
    await userEvent.selectOptions(select, 'sharegpt');
    await userEvent.click(screen.getByTestId('btn-export'));
    await screen.findByText(/7 examples exported successfully/i);
    const toasts = useToastStore.getState().toasts;
    expect(
      toasts.some((t) => t.kind === 'error' && /format conversion failed/i.test(t.message))
    ).toBe(true);
  });

  test('enabling split downloads train and validation files', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('preview'))
        return new Response(JSON.stringify(PREVIEW), { status: 200 });
      if (url.includes('/intelligence/export-training-data') && init?.method === 'POST') {
        const lines = Array.from({ length: 10 }, (_, i) => JSON.stringify({ i })).join('\n');
        return exportResponse(lines);
      }
      return new Response('{}', { status: 200 });
    });
    const user = userEvent.setup({ delay: null });
    renderPage();
    await screen.findByLabelText(/^format$/i);
    await user.click(screen.getByLabelText(/train \/ validation split/i));
    await user.click(screen.getByTestId('btn-export'));
    await vi.waitFor(() => expect(screen.queryByText(/7 examples exported successfully/i)).toBeInTheDocument());
    await vi.advanceTimersByTimeAsync(400);
    expect(URL.createObjectURL).toHaveBeenCalledTimes(2);
    vi.useRealTimers();
  });

  test('export history can be shown and cleared', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('preview'))
        return new Response(JSON.stringify(PREVIEW), { status: 200 });
      if (url.includes('/intelligence/export-training-data') && init?.method === 'POST')
        return exportResponse('{"a":1}');
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByLabelText(/^format$/i);
    await userEvent.click(screen.getByTestId('btn-export'));
    await screen.findByText(/7 examples exported successfully/i);

    // history toggle now shows a count badge and, when toggled, the panel appears
    const toggleButton = await screen.findByRole('button', { name: /hide export history/i });
    expect(within(toggleButton).getByText('1')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /hide export history/i }));
    expect(screen.queryByText(/^Export History$/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /show export history/i }));
    expect(await screen.findByText(/^Export History$/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /clear/i }));
    expect(screen.queryByText(/^Export History$/)).not.toBeInTheDocument();
  });
});
