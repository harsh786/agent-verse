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

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
  // Prevent jsdom from handling blob: URL navigation (causes process to hang)
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  // jsdom lacks createObjectURL — provide stubs
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
  test('renders export controls', () => {
    renderPage();
    expect(screen.getByLabelText(/format/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/minimum eval score/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /export/i })).toBeInTheDocument();
  });

  test('triggers export and shows the example count', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('{"a":1}', {
        status: 200,
        headers: {
          'Content-Type': 'application/x-ndjson',
          'Content-Disposition': 'attachment; filename="agentverse_training_openai_x.jsonl"',
          'X-Training-Examples': '7',
        },
      })
    );
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /export/i }));
    expect(await screen.findByText(/7 examples/i)).toBeInTheDocument();
    const call = f.mock.calls.find(([u]) =>
      String(u).includes('/intelligence/export-training-data')
    );
    expect(call).toBeTruthy();
    expect(String(call![0])).toContain('format=openai');
  });
});
