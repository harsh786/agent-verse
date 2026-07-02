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
  sessionStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ToolsPage', () => {
  test('renders three tabs: Code Runner, File Manager, Email', () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }));
    renderPage();
    expect(screen.getByRole('tab', { name: /code runner/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /file manager/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /email/i })).toBeInTheDocument();
  });

  test('defaults to code runner tab with textarea and language buttons', () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }));
    renderPage();
    expect(screen.getByRole('tab', { name: /code runner/i })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByLabelText(/^code$/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /python/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /javascript/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /bash/i })).toBeInTheDocument();
  });

  test('shows run code button disabled when code is empty', () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }));
    renderPage();
    expect(screen.getByRole('button', { name: /run code/i })).toBeDisabled();
  });

  test('runs code and shows stdout on success', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input).includes('/tools/execute-code'))
        return new Response(
          JSON.stringify({ stdout: 'hello world\n', stderr: '', exit_code: 0, success: true, timed_out: false, execution_time_ms: 42 }),
          { status: 200 }
        );
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.type(screen.getByLabelText(/^code$/i), "print('hello world')");
    await userEvent.click(screen.getByRole('button', { name: /run code/i }));
    // stdout is in a <pre> tag, not the textarea — use getAllByText and check for pre
    const elements = await screen.findAllByText(/hello world/);
    expect(elements.some(el => el.tagName === 'PRE')).toBe(true);
    expect(screen.getByText(/exit 0/i)).toBeInTheDocument();
    expect(screen.getByText(/42ms/)).toBeInTheDocument();
  });

  test('shows stderr section when code has errors', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input).includes('/tools/execute-code'))
        return new Response(
          JSON.stringify({ stdout: '', stderr: 'SyntaxError: invalid syntax', exit_code: 1, success: false, timed_out: false, execution_time_ms: 12 }),
          { status: 200 }
        );
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.type(screen.getByLabelText(/^code$/i), 'invalid{{{{');
    await userEvent.click(screen.getByRole('button', { name: /run code/i }));
    expect(await screen.findByText(/SyntaxError/)).toBeInTheDocument();
    expect(screen.getByText(/stderr/i)).toBeInTheDocument();
  });

  test('adds to execution history after run', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input).includes('/tools/execute-code'))
        return new Response(
          JSON.stringify({ stdout: 'ok', stderr: '', exit_code: 0, success: true, timed_out: false, execution_time_ms: 5 }),
          { status: 200 }
        );
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.type(screen.getByLabelText(/^code$/i), 'print("ok")');
    await userEvent.click(screen.getByRole('button', { name: /run code/i }));
    await screen.findByText('ok');
    expect(screen.getByText(/execution history/i)).toBeInTheDocument();
  });

  test('switches to file manager tab and shows workspace', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input).includes('/tools/files'))
        return new Response(
          JSON.stringify([{ name: 'hello.py', path: 'hello.py', type: 'file', size_bytes: 22, modified_at: 0 }]),
          { status: 200 }
        );
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /file manager/i }));
    expect(await screen.findByText('hello.py')).toBeInTheDocument();
    // "Workspace" heading in the card header
    expect(screen.getAllByText(/workspace/i).some(el => el.tagName === 'H2')).toBe(true);
  });

  test('file manager shows empty state when no files', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input).includes('/tools/files'))
        return new Response('[]', { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /file manager/i }));
    expect(await screen.findByText(/no files yet/i)).toBeInTheDocument();
  });

  test('email composer posts correct body to /tools/email/send', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      if (String(input).includes('/tools/email/send') && init?.method === 'POST')
        return new Response(JSON.stringify({ success: true, status: 'sent' }), { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /email/i }));
    await userEvent.type(screen.getByLabelText(/^to$/i), 'x@y.z');
    await userEvent.type(screen.getByLabelText(/^subject$/i), 'Hi');
    await userEvent.type(screen.getByLabelText(/^message$/i), 'Body text');
    await userEvent.click(screen.getByRole('button', { name: /send email/i }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([u, i]) => String(u).includes('/tools/email/send') && (i as RequestInit)?.method === 'POST'
      );
      expect(call).toBeTruthy();
      const body = JSON.parse(String((call![1] as RequestInit).body));
      expect(body).toMatchObject({ to: 'x@y.z', subject: 'Hi', body: 'Body text' });
    });
  });

  test('email send button is disabled until To and Subject are filled', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }));
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /email/i }));
    // Wait for email tab to render
    expect(await screen.findByRole('button', { name: /send email/i })).toBeDisabled();
  });

  test('sent items appear after successful send', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      if (String(input).includes('/tools/email/send') && init?.method === 'POST')
        return new Response(JSON.stringify({ success: true }), { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /email/i }));
    await userEvent.type(screen.getByLabelText(/^to$/i), 'a@b.com');
    await userEvent.type(screen.getByLabelText(/^subject$/i), 'Test subject');
    await userEvent.type(screen.getByLabelText(/^message$/i), 'hi');
    await userEvent.click(screen.getByRole('button', { name: /send email/i }));
    await waitFor(() => expect(screen.getByText('Test subject')).toBeInTheDocument());
  });
});
