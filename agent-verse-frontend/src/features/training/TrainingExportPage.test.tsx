import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
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
  ],
};

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
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
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input).includes('preview'))
        return new Response(JSON.stringify(PREVIEW), { status: 200 });
      return new Response('{}', { status: 200 });
    });
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
        return new Response('{"a":1}', {
          status: 200,
          headers: {
            'Content-Type': 'application/x-ndjson',
            'Content-Disposition': 'attachment; filename="agentverse_training_openai_x.jsonl"',
            'X-Training-Examples': '7',
          },
        });
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
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input).includes('preview'))
        return new Response(JSON.stringify(PREVIEW), { status: 200 });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    expect(await screen.findByTestId('score-distribution')).toBeInTheDocument();
  });

  test('shows sample goal text after preview loads', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input).includes('preview'))
        return new Response(JSON.stringify(PREVIEW), { status: 200 });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    expect(await screen.findByText(/Triage Jira issues/i)).toBeInTheDocument();
  });
});
