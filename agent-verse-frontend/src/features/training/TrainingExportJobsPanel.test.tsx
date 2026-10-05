import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type { TrainingExportJob } from '@/lib/api/client';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { TrainingExportJobsPanel } from './TrainingExportJobsPanel';

type Fmt = 'openai' | 'anthropic' | 'llama' | 'sharegpt';

function renderPanel(props: { format?: Fmt; pollMs?: number } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TrainingExportJobsPanel
        format={props.format ?? 'openai'}
        minScore={0.85}
        pollMs={props.pollMs ?? 60_000}
      />
    </QueryClientProvider>
  );
}

function job(over: Partial<TrainingExportJob>): TrainingExportJob {
  return {
    job_id: 'j1',
    status: 'queued',
    format: 'openai',
    min_score: 0.8,
    limit: 10000,
    example_count: null,
    has_file: false,
    error: null,
    created_at: '2026-10-05T10:00:00+00:00',
    completed_at: null,
    ...over,
  };
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

beforeEach(() => {
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
  localStorage.clear();
});

describe('TrainingExportJobsPanel', () => {
  test('lists jobs with their status; only a finished job offers a download', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      json({
        jobs: [
          job({ job_id: 'jq', status: 'queued' }),
          job({ job_id: 'jr', status: 'running' }),
          job({ job_id: 'jc', status: 'complete', has_file: true, example_count: 1234 }),
        ],
      })
    );
    renderPanel();
    const queued = await screen.findByTestId('export-job-jq');
    expect(within(queued).getByText(/queued/i)).toBeInTheDocument();
    expect(within(screen.getByTestId('export-job-jr')).getByText(/running/i)).toBeInTheDocument();
    const done = screen.getByTestId('export-job-jc');
    expect(within(done).getByText(/1,?234 examples/i)).toBeInTheDocument();
    expect(within(done).getByRole('button', { name: /download/i })).toBeInTheDocument();
    expect(within(queued).queryByRole('button', { name: /download/i })).toBeNull();
  });

  test('a failed job shows its recorded error and no download', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      json({ jobs: [job({ job_id: 'jf', status: 'failed', error: 'OSError: bucket gone' })] })
    );
    renderPanel();
    const row = await screen.findByTestId('export-job-jf');
    expect(within(row).getByRole('alert')).toHaveTextContent(/failed.*OSError: bucket gone/i);
    expect(within(row).queryByRole('button', { name: /download/i })).toBeNull();
  });

  test('an expired job says its file was deleted and offers no download (NF-17)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      json({ jobs: [job({ job_id: 'je', status: 'expired', example_count: 12 })] })
    );
    renderPanel();
    const row = await screen.findByTestId('export-job-je');
    expect(row).toHaveTextContent(/expired.*file deleted/i);
    expect(within(row).queryByRole('button', { name: /download/i })).toBeNull();
  });

  test('a failed job without a recorded reason says so instead of looking fine', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      json({ jobs: [job({ job_id: 'jf', status: 'failed', error: null })] })
    );
    renderPanel();
    const row = await screen.findByTestId('export-job-jf');
    expect(within(row).getByRole('alert')).toHaveTextContent(/failed.*no reason recorded/i);
  });

  test('starting a job posts the filters and shows the queued job', async () => {
    let created = false;
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (init?.method === 'POST') {
        created = true;
        return json(job({ job_id: 'jnew', status: 'queued', format: 'anthropic' }), 202);
      }
      if (url.includes('/jobs')) return json({ jobs: created ? [job({ job_id: 'jnew' })] : [] });
      return json({});
    });
    renderPanel({ format: 'anthropic' });
    expect(await screen.findByText(/no background exports yet/i)).toBeInTheDocument();
    await userEvent.click(screen.getByTestId('btn-start-export-job'));
    expect(await screen.findByTestId('export-job-jnew')).toBeInTheDocument();
    const post = f.mock.calls.find(([, init]) => init?.method === 'POST');
    const url = String(post?.[0]);
    expect(url).toContain('/intelligence/export-training-data/jobs?');
    expect(url).toContain('format=anthropic');
    expect(url).toContain('min_score=0.85');
    expect(url).toContain('limit=100000');
  });

  test('a refused start (429) is shown with the server reason', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      if (init?.method === 'POST')
        return json({ detail: 'at most 3 export jobs may be active at once' }, 429);
      return json({ jobs: [] });
    });
    renderPanel();
    await screen.findByText(/no background exports yet/i);
    await userEvent.click(screen.getByTestId('btn-start-export-job'));
    expect(await screen.findByTestId('export-job-start-error')).toHaveTextContent(
      /at most 3 export jobs may be active at once/
    );
  });

  test('jobs that cannot be listed are reported as unavailable, not as "none yet"', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      json({ detail: 'Object storage is not configured for export jobs' }, 503)
    );
    renderPanel();
    expect(await screen.findByTestId('export-jobs-error')).toHaveTextContent(
      /Object storage is not configured for export jobs/
    );
    expect(screen.queryByText(/no background exports yet/i)).toBeNull();
  });

  test('downloading a finished job fetches its file and saves it', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith('/download'))
        return new Response('{"a":1}\n', {
          status: 200,
          headers: { 'Content-Disposition': 'attachment; filename="agentverse_training_jc.jsonl"' },
        });
      return json({
        jobs: [job({ job_id: 'jc', status: 'complete', has_file: true, example_count: 1 })],
      });
    });
    renderPanel();
    const row = await screen.findByTestId('export-job-jc');
    await userEvent.click(within(row).getByRole('button', { name: /download/i }));
    await waitFor(() => expect(HTMLAnchorElement.prototype.click).toHaveBeenCalled());
    expect(f.mock.calls.some(([u]) => String(u).endsWith('/jobs/jc/download'))).toBe(true);
  });

  test('a download that fails shows an error toast', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input).endsWith('/download'))
        return json({ detail: 'Export file unavailable; please retry' }, 503);
      return json({
        jobs: [job({ job_id: 'jc', status: 'complete', has_file: true, example_count: 1 })],
      });
    });
    renderPanel();
    const row = await screen.findByTestId('export-job-jc');
    await userEvent.click(within(row).getByRole('button', { name: /download/i }));
    await waitFor(() =>
      expect(
        useToastStore.getState().toasts.some((t) => /Export file unavailable/.test(t.message))
      ).toBe(true)
    );
    expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled();
  });

  test('an active job is polled until it finishes', async () => {
    let calls = 0;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
      calls += 1;
      return json({
        jobs: [
          calls < 3
            ? job({ job_id: 'jp', status: 'running' })
            : job({ job_id: 'jp', status: 'complete', has_file: true, example_count: 9 }),
        ],
      });
    });
    renderPanel({ pollMs: 20 });
    const row = await screen.findByTestId('export-job-jp');
    expect(within(row).getByText(/running/i)).toBeInTheDocument();
    expect(await within(row).findByText(/9 examples/i)).toBeInTheDocument();
  });

  test('client-side formats cannot start a server job', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({ jobs: [] }));
    renderPanel({ format: 'llama' });
    await screen.findByText(/no background exports yet/i);
    expect(screen.getByTestId('btn-start-export-job')).toBeDisabled();
    expect(screen.getByText(/OpenAI or Anthropic/i)).toBeInTheDocument();
  });
});
