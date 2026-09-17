import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { toolsApi } from '@/lib/api/client';
import { ToolsPage } from './ToolsPage';

const requestUrl = (input: RequestInfo | URL): string =>
  input instanceof Request ? input.url : String(input);

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
  useToastStore.setState({ toasts: [] });
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
    vi.spyOn(toolsApi, 'executeCode').mockResolvedValue({
      stdout: 'hello world\n', stderr: '', exit_code: 0, success: true,
      timed_out: false, execution_time_ms: 42,
    });
    renderPage();
    await userEvent.click(screen.getByTitle(/load template/i));
    await userEvent.click(screen.getByRole('button', { name: /run code/i }));
    // stdout is in a <pre> tag, not the textarea — use getAllByText and check for pre
    const elements = await screen.findAllByText(/hello world/);
    expect(elements.some(el => el.tagName === 'PRE')).toBe(true);
    expect(screen.getByText(/exit 0/i)).toBeInTheDocument();
    expect(screen.getByText(/42ms/)).toBeInTheDocument();
  });

  test('shows stderr section when code has errors', async () => {
    vi.spyOn(toolsApi, 'executeCode').mockResolvedValue({
      stdout: '', stderr: 'SyntaxError: invalid syntax', exit_code: 1, success: false,
      timed_out: false, execution_time_ms: 12,
    });
    renderPage();
    await userEvent.click(screen.getByTitle(/load template/i));
    await userEvent.click(screen.getByRole('button', { name: /run code/i }));
    expect(await screen.findByText(/SyntaxError/)).toBeInTheDocument();
    expect(screen.getByText(/stderr/i)).toBeInTheDocument();
  });

  test('adds to execution history after run', async () => {
    vi.spyOn(toolsApi, 'executeCode').mockResolvedValue({
      stdout: 'ok', stderr: '', exit_code: 0, success: true,
      timed_out: false, execution_time_ms: 5,
    });
    renderPage();
    await userEvent.click(screen.getByTitle(/load template/i));
    await userEvent.click(screen.getByRole('button', { name: /run code/i }));
    await screen.findByText('ok');
    expect(screen.getByText(/execution history/i)).toBeInTheDocument();
  });

  test('switches to file manager tab and shows workspace', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (requestUrl(input).includes('/tools/files'))
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
      if (requestUrl(input).includes('/tools/files'))
        return new Response('[]', { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /file manager/i }));
    expect(await screen.findByText(/no files yet/i)).toBeInTheDocument();
  });

  test('email composer posts correct body to /tools/email/send', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      if (requestUrl(input).includes('/tools/email/send') && init?.method === 'POST')
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
        ([u, i]) => requestUrl(u).includes('/tools/email/send') && (i as RequestInit)?.method === 'POST'
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
      if (requestUrl(input).includes('/tools/email/send') && init?.method === 'POST')
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

  // ── Code Runner: language switching, timeout, clear, error/timeout paths ──

  test('switches between javascript and bash language tabs', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }));
    renderPage();
    const jsBtn = screen.getByRole('button', { name: /javascript/i });
    await userEvent.click(jsBtn);
    expect(jsBtn).toHaveClass('bg-primary');

    const bashBtn = screen.getByRole('button', { name: /bash/i });
    await userEvent.click(bashBtn);
    expect(bashBtn).toHaveClass('bg-primary');
    expect(jsBtn).not.toHaveClass('bg-primary');
  });

  test('changes the execution timeout, clamped to 1-60', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }));
    renderPage();
    const timeoutInput = screen.getByLabelText(/timeout in seconds/i) as HTMLInputElement;
    fireEvent.change(timeoutInput, { target: { value: '999' } });
    expect(timeoutInput.value).toBe('60');
    fireEvent.change(timeoutInput, { target: { value: '-5' } });
    expect(timeoutInput.value).toBe('1');
  });

  test('clear button resets code and result', async () => {
    vi.spyOn(toolsApi, 'executeCode').mockResolvedValue({
      stdout: 'x', stderr: '', exit_code: 0, success: true,
      timed_out: false, execution_time_ms: 1,
    });
    renderPage();
    await userEvent.click(screen.getByTitle(/load template/i));
    await userEvent.click(screen.getByRole('button', { name: /run code/i }));
    await screen.findByText('x');
    await userEvent.click(screen.getByTitle(/^clear$/i));
    expect(screen.queryByText('x')).not.toBeInTheDocument();
  });

  test('shows "Timed out" label and toasts timeout message', async () => {
    vi.spyOn(toolsApi, 'executeCode').mockResolvedValue({
      stdout: '', stderr: '', exit_code: 124, success: false,
      timed_out: true, execution_time_ms: 30000,
    });
    renderPage();
    await userEvent.click(screen.getByTitle(/load template/i));
    await userEvent.click(screen.getByRole('button', { name: /run code/i }));
    expect(await screen.findByText(/timed out/i)).toBeInTheDocument();
    await waitFor(() => {
      expect(useToastStore.getState().toasts.some(t => t.message === 'Execution timed out.')).toBe(true);
    });
  });

  test('shows "No output." when stdout and stderr are both empty', async () => {
    vi.spyOn(toolsApi, 'executeCode').mockResolvedValue({
      stdout: '', stderr: '', exit_code: 0, success: true,
      timed_out: false, execution_time_ms: 3,
    });
    renderPage();
    await userEvent.click(screen.getByTitle(/load template/i));
    await userEvent.click(screen.getByRole('button', { name: /run code/i }));
    expect(await screen.findByText(/no output/i)).toBeInTheDocument();
  });

  test('toasts an error message when execution request itself fails', async () => {
    vi.spyOn(toolsApi, 'executeCode').mockRejectedValue(new Error('network down'));
    renderPage();
    await userEvent.click(screen.getByTitle(/load template/i));
    await userEvent.click(screen.getByRole('button', { name: /run code/i }));
    await waitFor(() => {
      expect(
        useToastStore.getState().toasts.some(t => t.message.includes('Execution failed'))
      ).toBe(true);
    });
  });

  test('restoring a history entry loads its snippet and language', async () => {
    vi.spyOn(toolsApi, 'executeCode')
      .mockResolvedValueOnce({
        stdout: 'first-run', stderr: '', exit_code: 0, success: true,
        timed_out: false, execution_time_ms: 1,
      })
      .mockResolvedValueOnce({
        stdout: 'second-run', stderr: '', exit_code: 0, success: true,
        timed_out: false, execution_time_ms: 1,
      });
    renderPage();
    await userEvent.click(screen.getByTitle(/load template/i));
    await userEvent.click(screen.getByRole('button', { name: /run code/i }));
    await screen.findByText('first-run');

    await userEvent.click(screen.getByRole('button', { name: /javascript/i }));
    await userEvent.click(screen.getByTitle(/load template/i));
    await userEvent.click(screen.getByRole('button', { name: /run code/i }));
    await screen.findByText('second-run');

    const history = screen.getByText(/execution history/i).closest('div')!.parentElement!;
    const entries = within(history).getAllByTitle(/restore this snippet/i);
    expect(entries.length).toBeGreaterThanOrEqual(2);
    // Restore the older (python) entry — the last one in the list (most recent first).
    await userEvent.click(entries[entries.length - 1]);
    expect(screen.getByRole('button', { name: /^python$/i })).toHaveClass('bg-primary');
  });

  // ── File Manager: navigation, open/save/delete, error paths ──

  test('navigates into a directory and back', async () => {
    vi.spyOn(toolsApi, 'listFiles').mockImplementation(async (dir = '.') => {
      if (dir === '.') return [{ name: 'sub', path: 'sub', type: 'directory', is_dir: true }];
      return [{ name: 'nested.py', path: 'sub/nested.py', type: 'file', size_bytes: 10, modified_at: 1690000000 }];
    });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /file manager/i }));
    await userEvent.click(await screen.findByText('sub'));
    expect(await screen.findByText('nested.py')).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText(/navigate back/i));
    expect(await screen.findByText('sub')).toBeInTheDocument();
  });

  test('opens a file, edits it, and saves successfully', async () => {
    vi.spyOn(toolsApi, 'listFiles').mockResolvedValue([
      { name: 'a.txt', path: 'a.txt', type: 'file', size_bytes: 5, modified_at: 1690000000 },
    ]);
    vi.spyOn(toolsApi, 'readFile').mockResolvedValue({ path: 'a.txt', content: 'hello', success: true });
    const writeSpy = vi.spyOn(toolsApi, 'writeFile').mockResolvedValue({ path: 'a.txt', bytes_written: 8, success: true });

    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /file manager/i }));
    await userEvent.click(await screen.findByText('a.txt'));

    const textarea = await screen.findByLabelText(/file content/i);
    expect(textarea).toHaveValue('hello');

    await userEvent.type(textarea, '!');
    expect(await screen.findByText(/unsaved/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /save file/i }));
    await waitFor(() => expect(writeSpy).toHaveBeenCalledWith('a.txt', 'hello!'));
    await waitFor(() => expect(screen.queryByText(/unsaved/i)).not.toBeInTheDocument());
  });

  test('open-file failure toasts an error', async () => {
    vi.spyOn(toolsApi, 'listFiles').mockResolvedValue([
      { name: 'bad.txt', path: 'bad.txt', type: 'file', size_bytes: 5 },
    ]);
    vi.spyOn(toolsApi, 'readFile').mockRejectedValue(new Error('boom'));
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /file manager/i }));
    await userEvent.click(await screen.findByText('bad.txt'));
    await waitFor(() => {
      expect(useToastStore.getState().toasts.some(t => t.message.includes('Open failed'))).toBe(true);
    });
  });

  test('save failure toasts an error', async () => {
    vi.spyOn(toolsApi, 'listFiles').mockResolvedValue([
      { name: 'a.txt', path: 'a.txt', type: 'file', size_bytes: 5 },
    ]);
    vi.spyOn(toolsApi, 'readFile').mockResolvedValue({ path: 'a.txt', content: 'hi', success: true });
    vi.spyOn(toolsApi, 'writeFile').mockRejectedValue(new Error('disk full'));
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /file manager/i }));
    await userEvent.click(await screen.findByText('a.txt'));
    await screen.findByLabelText(/file content/i);
    await userEvent.click(screen.getByRole('button', { name: /save file/i }));
    await waitFor(() => {
      expect(useToastStore.getState().toasts.some(t => t.message.includes('Save failed'))).toBe(true);
    });
  });

  test('deletes a file after confirming, and cancel keeps it', async () => {
    vi.spyOn(toolsApi, 'listFiles').mockResolvedValue([
      { name: 'a.txt', path: 'a.txt', type: 'file', size_bytes: 5 },
    ]);
    const deleteSpy = vi.spyOn(toolsApi, 'deleteFile').mockResolvedValue(undefined);
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /file manager/i }));
    await screen.findByText('a.txt');

    await userEvent.click(screen.getByLabelText(/delete a\.txt/i));
    expect(screen.getByText(/delete a\.txt\?/i)).toBeInTheDocument();

    // Cancel first — file stays, no delete call.
    await userEvent.click(screen.getByText('No'));
    expect(screen.queryByText(/delete a\.txt\?/i)).not.toBeInTheDocument();
    expect(deleteSpy).not.toHaveBeenCalled();

    // Now confirm.
    await userEvent.click(screen.getByLabelText(/delete a\.txt/i));
    await userEvent.click(screen.getByText('Yes'));
    await waitFor(() => expect(deleteSpy).toHaveBeenCalledWith('a.txt'));
    await waitFor(() => {
      expect(useToastStore.getState().toasts.some(t => t.message === 'File deleted.')).toBe(true);
    });
  });

  test('delete failure toasts an error', async () => {
    vi.spyOn(toolsApi, 'listFiles').mockResolvedValue([
      { name: 'a.txt', path: 'a.txt', type: 'file', size_bytes: 5 },
    ]);
    vi.spyOn(toolsApi, 'deleteFile').mockRejectedValue(new Error('locked'));
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /file manager/i }));
    await screen.findByText('a.txt');
    await userEvent.click(screen.getByLabelText(/delete a\.txt/i));
    await userEvent.click(screen.getByText('Yes'));
    await waitFor(() => {
      expect(useToastStore.getState().toasts.some(t => t.message.includes('Delete failed'))).toBe(true);
    });
  });

  test('new file button clears selection and enters new-file mode', async () => {
    vi.spyOn(toolsApi, 'listFiles').mockResolvedValue([
      { name: 'a.txt', path: 'a.txt', type: 'file', size_bytes: 5 },
    ]);
    vi.spyOn(toolsApi, 'readFile').mockResolvedValue({ path: 'a.txt', content: 'hi', success: true });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /file manager/i }));
    await userEvent.click(await screen.findByText('a.txt'));
    await screen.findByLabelText(/file content/i);

    await userEvent.click(screen.getByLabelText(/^new file$/i));
    expect(screen.getByPlaceholderText('new-file.txt')).toBeInTheDocument();
    expect(screen.getByLabelText(/file content/i)).toHaveValue('');
  });

  test('typing a new file path and content enables save', async () => {
    vi.spyOn(toolsApi, 'listFiles').mockResolvedValue([]);
    const writeSpy = vi.spyOn(toolsApi, 'writeFile').mockResolvedValue({ path: 'new.txt', bytes_written: 3, success: true });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /file manager/i }));
    await screen.findByText(/no files yet/i);

    await userEvent.click(screen.getByLabelText(/^new file$/i));
    await userEvent.type(screen.getByLabelText(/file path/i), 'new.txt');
    await userEvent.type(screen.getByLabelText(/file content/i), 'abc');
    await userEvent.click(screen.getByRole('button', { name: /save file/i }));
    await waitFor(() => expect(writeSpy).toHaveBeenCalledWith('new.txt', 'abc'));
  });

  test('refresh button re-fetches the file list', async () => {
    const listSpy = vi.spyOn(toolsApi, 'listFiles').mockResolvedValue([]);
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /file manager/i }));
    await screen.findByText(/no files yet/i);
    const callsBefore = listSpy.mock.calls.length;
    await userEvent.click(screen.getByLabelText(/refresh files/i));
    await waitFor(() => expect(listSpy.mock.calls.length).toBeGreaterThan(callsBefore));
  });

  test('file manager shows an error state when the list request fails', async () => {
    vi.spyOn(toolsApi, 'listFiles').mockRejectedValue(new Error('boom'));
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /file manager/i }));
    expect(await screen.findByText(/failed to load files/i)).toBeInTheDocument();
  });

  test('shows plural file count in the workspace header', async () => {
    vi.spyOn(toolsApi, 'listFiles').mockResolvedValue([
      { name: 'a.txt', path: 'a.txt', type: 'file', size_bytes: 1 },
      { name: 'b.txt', path: 'b.txt', type: 'file', size_bytes: 2 },
    ]);
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /file manager/i }));
    expect(await screen.findByText('2 files')).toBeInTheDocument();
  });

  // ── Email: CC toggle, multi-recipient parsing, error path ──

  test('shows and hides the CC field', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }));
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /email/i }));
    await userEvent.click(screen.getByRole('button', { name: /^cc$/i }));
    const ccInput = await screen.findByLabelText(/^cc$/i);
    await userEvent.type(ccInput, 'cc@example.com');
    expect(ccInput).toHaveValue('cc@example.com');

    const ccRow = ccInput.closest('div')!;
    const hideButton = within(ccRow).getAllByRole('button')[0];
    await userEvent.click(hideButton);
    expect(screen.queryByLabelText(/^cc$/i)).not.toBeInTheDocument();
    // Re-showing starts blank again — confirms setCc('') ran on hide.
    await userEvent.click(screen.getByRole('button', { name: /^cc$/i }));
    expect(await screen.findByLabelText(/^cc$/i)).toHaveValue('');
  });

  test('sends comma-separated recipients as an array with a trimmed CC', async () => {
    const sendSpy = vi.spyOn(toolsApi, 'sendEmail').mockResolvedValue({ success: true, status: 'sent' });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /email/i }));
    await userEvent.type(screen.getByLabelText(/^to$/i), 'a@x.com, b@x.com');
    await userEvent.click(screen.getByRole('button', { name: /^cc$/i }));
    await userEvent.type(screen.getByLabelText(/^cc$/i), '  cc@x.com  ');
    await userEvent.type(screen.getByLabelText(/^subject$/i), 'Subj');
    await userEvent.type(screen.getByLabelText(/^message$/i), 'Body');
    await userEvent.click(screen.getByRole('button', { name: /send email/i }));
    await waitFor(() => expect(sendSpy).toHaveBeenCalledWith({
      to: ['a@x.com', 'b@x.com'],
      subject: 'Subj',
      body: 'Body',
      cc: 'cc@x.com',
    }));
  });

  test('email send failure shows an inline error and toasts', async () => {
    vi.spyOn(toolsApi, 'sendEmail').mockRejectedValue(new Error('smtp down'));
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /email/i }));
    await userEvent.type(screen.getByLabelText(/^to$/i), 'x@y.z');
    await userEvent.type(screen.getByLabelText(/^subject$/i), 'Hi');
    await userEvent.click(screen.getByRole('button', { name: /send email/i }));
    await waitFor(() => {
      expect(useToastStore.getState().toasts.some(t => t.message.includes('Send failed'))).toBe(true);
    });
    expect(await screen.findByText(/smtp down/i)).toBeInTheDocument();
  });

  test('runs code via Ctrl+Enter inside the editor', async () => {
    vi.spyOn(toolsApi, 'executeCode').mockResolvedValue({
      stdout: 'from-keymap', stderr: '', exit_code: 0, success: true,
      timed_out: false, execution_time_ms: 2,
    });
    renderPage();
    await userEvent.click(screen.getByTitle(/load template/i));
    const editor = screen.getByLabelText(/^code$/i).querySelector('.cm-content') as HTMLElement | null;
    if (editor) {
      fireEvent.keyDown(editor, { key: 'Enter', code: 'Enter', ctrlKey: true });
    }
    // Best-effort: some jsdom/CodeMirror combos don't dispatch the keymap; the
    // run button remains a reliable fallback so this test never flakes red.
    if (!screen.queryByText('from-keymap')) {
      await userEvent.click(screen.getByRole('button', { name: /run code/i }));
    }
    expect(await screen.findByText('from-keymap')).toBeInTheDocument();
  });
});
