import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ToolsPage } from './ToolsPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ToolsPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ToolsPage', () => {
  test('renders tabs and defaults to code runner', () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }));
    renderPage();
    expect(screen.getByRole('tab', { name: /code runner/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /email/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/code/i)).toBeInTheDocument();
  });

  test('runs code and shows stdout + exit code', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/tools/execute-code'))
        return new Response(
          JSON.stringify({ stdout: 'hello world', stderr: '', exit_code: 0, success: true, timed_out: false, execution_time_ms: 9 }),
          { status: 200 }
        );
      if (url.includes('/tools/files')) return new Response('[]', { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.type(screen.getByLabelText(/code/i), "print('hi')");
    await userEvent.click(screen.getByRole('button', { name: /run code/i }));
    expect(await screen.findByText('hello world')).toBeInTheDocument();
    expect(screen.getByText(/exit 0/i)).toBeInTheDocument();
  });

  test('email composer posts to /tools/email/send', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/tools/email/send') && init?.method === 'POST')
        return new Response(JSON.stringify({ status: 'sent' }), { status: 200 });
      if (url.includes('/tools/files')) return new Response('[]', { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /email/i }));
    await userEvent.type(screen.getByLabelText(/to/i), 'x@y.z');
    await userEvent.type(screen.getByLabelText(/subject/i), 'Hi');
    await userEvent.type(screen.getByLabelText(/message/i), 'Body');
    await userEvent.click(screen.getByRole('button', { name: /send email/i }));
    await waitFor(() => {
      const call = f.mock.calls.find(
        ([u, i]) => String(u).includes('/tools/email/send') && (i as RequestInit)?.method === 'POST'
      );
      expect(call).toBeTruthy();
      expect(JSON.parse(String((call![1] as RequestInit).body))).toMatchObject({
        to: 'x@y.z',
        subject: 'Hi',
        body: 'Body',
      });
    });
  });
});
