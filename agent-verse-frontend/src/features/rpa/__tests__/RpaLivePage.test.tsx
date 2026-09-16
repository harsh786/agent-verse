import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { RpaLivePage } from '../RpaLivePage';

const SESSION = (overrides = {}) => ({
  session_id: 'sess-001',
  status: 'active',
  created_at: new Date().toISOString(),
  ...overrides,
});

const RPA_TOOL_WITH_SCHEMA = {
  name: 'rpa_navigate',
  description: 'Navigate to a URL',
  risk: 'low',
  input_schema: {
    properties: {
      url: { description: 'Target URL' },
      wait_until: { enum: ['load', 'domcontentloaded'] },
      headless: { type: 'boolean' },
    },
  },
};
const RPA_TOOL_NO_SCHEMA = { name: 'rpa_click', description: 'Click', risk: 'high' };

function mockFetch(opts: {
  sessions?: unknown[];
  executeResult?: unknown;
  getSelectorResult?: unknown | 'reject';
  screenshot?: 'ok' | 'none';
} = {}) {
  const sessions = opts.sessions ?? [SESSION()];
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init as RequestInit | undefined)?.method ?? 'GET';
    if (url.includes('/rpa/sessions') && method === 'POST')
      return new Response(JSON.stringify({ session_id: 'new-sess', status: 'active', created_at: new Date().toISOString() }), { status: 201 });
    if (url.includes('/rpa/sessions/') && method === 'DELETE')
      return new Response(null, { status: 204 });
    if (url.includes('/rpa/sessions/') && url.includes('/screenshot')) {
      if (opts.screenshot === 'none') return new Response(JSON.stringify({ session_id: 'sess-001', screenshot_data_uri: '' }), { status: 200 });
      return new Response(JSON.stringify({ session_id: 'sess-001', screenshot_data_uri: 'data:image/png;base64,abc', url: 'https://example.com', timestamp: new Date().toISOString() }), { status: 200 });
    }
    if (url.includes('/rpa/sessions/') && url.includes('/takeover'))
      return new Response(JSON.stringify({ session_id: 'sess-001', status: 'awaiting_human', message: 'Takeover requested' }), { status: 200 });
    if (url.includes('/rpa/sessions') && method === 'GET')
      return new Response(JSON.stringify(sessions), { status: 200 });
    if (url.includes('/rpa/tools'))
      return new Response(JSON.stringify({ tools: [RPA_TOOL_WITH_SCHEMA, RPA_TOOL_NO_SCHEMA] }), { status: 200 });
    if (url.includes('/rpa/execute')) {
      const body = JSON.parse(String(init?.body ?? '{}'));
      if (body.tool_name === 'rpa_get_selector') {
        if (opts.getSelectorResult === 'reject') return new Response('error', { status: 500 });
        return new Response(JSON.stringify(opts.getSelectorResult ?? { success: true, output: '#save-btn', tool_name: 'rpa_get_selector' }), { status: 200 });
      }
      return new Response(JSON.stringify(opts.executeResult ?? { success: true, output: 'done', tool_name: body.tool_name, duration_ms: 42 }), { status: 200 });
    }
    return new Response('[]', { status: 200 });
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <RpaLivePage />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

async function renderWithActiveSession(opts?: Parameters<typeof mockFetch>[0]) {
  const spy = mockFetch(opts);
  renderPage();
  await waitFor(() => screen.getByText(/sess-001/));
  await userEvent.click(screen.getAllByText(/sess-001/)[0]);
  await waitFor(() => expect(screen.getByTestId('viewport-screenshot')).toBeInTheDocument());
  return spy;
}

beforeEach(() => {
  sessionStorage.setItem('av_api_key', 'test-key');
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    left: 0, top: 0, right: 1280, bottom: 720, width: 1280, height: 720, x: 0, y: 0, toJSON: () => ({}),
  } as DOMRect);
});
afterEach(() => vi.restoreAllMocks());

describe('RpaLivePage — sessions list', () => {
  test('renders heading', () => {
    mockFetch({ sessions: [] });
    renderPage();
    expect(screen.getByRole('heading', { name: /rpa live/i })).toBeInTheDocument();
  });

  test('shows empty session state', async () => {
    mockFetch({ sessions: [] });
    renderPage();
    await waitFor(() => expect(screen.getByText(/no active sessions/i)).toBeInTheDocument());
  });

  test('lists sessions', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByText(/sess-001/)).toBeInTheDocument());
  });

  test('shows session status badge', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => screen.getByText(/sess-001/));
    expect(screen.getByText('active')).toBeInTheDocument();
  });

  test('New Session button calls POST /rpa/sessions', async () => {
    const fetchSpy = mockFetch({ sessions: [] });
    renderPage();
    await waitFor(() => expect(screen.getByRole('button', { name: /new session/i })).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /new session/i }));
    await waitFor(() => {
      const post = fetchSpy.mock.calls.find(([u, i]) => String(u).includes('/rpa/sessions') && (i as RequestInit)?.method === 'POST');
      expect(post).toBeTruthy();
    });
  });

  test('"Start a new one" link in the empty session panel also creates a session', async () => {
    const fetchSpy = mockFetch({ sessions: [] });
    renderPage();
    await waitFor(() => expect(screen.getByText(/start a new one/i)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/start a new one/i));
    await waitFor(() => {
      const post = fetchSpy.mock.calls.find(([u, i]) => String(u).includes('/rpa/sessions') && (i as RequestInit)?.method === 'POST');
      expect(post).toBeTruthy();
    });
  });

  test('shows empty state when no session selected', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => screen.getByText(/sess-001/));
    expect(screen.getByText(/no session selected/i)).toBeInTheDocument();
  });

  test('switching between two sessions updates the active control bar', async () => {
    mockFetch({ sessions: [SESSION(), SESSION({ session_id: 'sess-002' })] });
    renderPage();
    await waitFor(() => screen.getByText(/sess-001/));
    await userEvent.click(screen.getAllByText(/sess-001/)[0]);
    await waitFor(() => expect(screen.getByTestId('viewport-screenshot')).toBeInTheDocument());
    await userEvent.click(screen.getAllByText(/sess-002/)[0]);
    await waitFor(() => expect(screen.getByTestId('viewport-screenshot')).toBeInTheDocument());
  });
});

describe('RpaLivePage — viewport interactions', () => {
  test('clicking the screenshot sends an rpa_click and logs the action', async () => {
    const spy = await renderWithActiveSession();
    fireEvent.click(screen.getByTestId('viewport-screenshot'), { clientX: 100, clientY: 50 });
    await waitFor(() => {
      const call = spy.mock.calls.find(([u, i]) => {
        if (!String(u).includes('/rpa/execute')) return false;
        const body = JSON.parse(String((i as RequestInit)?.body ?? '{}'));
        return body.tool_name === 'rpa_click';
      });
      expect(call).toBeTruthy();
    });
    expect(await screen.findByText(/rpa_click/i)).toBeInTheDocument();
  });

  test('refresh screenshot button re-fetches the screenshot', async () => {
    const spy = await renderWithActiveSession();
    const before = spy.mock.calls.filter(([u]) => String(u).includes('/screenshot')).length;
    await userEvent.click(screen.getByLabelText(/refresh screenshot/i));
    await waitFor(() => {
      const after = spy.mock.calls.filter(([u]) => String(u).includes('/screenshot')).length;
      expect(after).toBeGreaterThan(before);
    });
  });

  test('shows "no screenshot available" when the session has none', async () => {
    mockFetch({ screenshot: 'none' });
    renderPage();
    await waitFor(() => screen.getByText(/sess-001/));
    await userEvent.click(screen.getAllByText(/sess-001/)[0]);
    expect(await screen.findByText(/no screenshot available/i)).toBeInTheDocument();
  });

  test('toggling keyboard capture mode and pressing a key sends rpa_type', async () => {
    const spy = await renderWithActiveSession();
    await userEvent.click(screen.getByRole('button', { name: /^keyboard$/i }));
    expect(screen.getByRole('button', { name: /keyboard: on/i })).toBeInTheDocument();
    expect(screen.getByText(/keyboard capture/i)).toBeInTheDocument();

    const viewport = screen.getByTestId('viewport-screenshot').closest('[tabindex]')!;
    fireEvent.keyDown(viewport, { key: 'a' });
    await waitFor(() => {
      const call = spy.mock.calls.find(([u, i]) => {
        if (!String(u).includes('/rpa/execute')) return false;
        const body = JSON.parse(String((i as RequestInit)?.body ?? '{}'));
        return body.tool_name === 'rpa_type' && body.arguments?.text === 'a';
      });
      expect(call).toBeTruthy();
    });

    // Escape exits keyboard capture mode
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(screen.getByRole('button', { name: /^keyboard$/i })).toBeInTheDocument());
  });

  test('wheel events forward rpa_scroll with nonzero deltas only', async () => {
    const spy = await renderWithActiveSession();
    const viewport = screen.getByTestId('viewport-screenshot').closest('[tabindex]')!;
    fireEvent.wheel(viewport, { deltaX: 0, deltaY: 0 });
    fireEvent.wheel(viewport, { deltaX: 5, deltaY: 10 });
    await waitFor(() => {
      const call = spy.mock.calls.find(([u, i]) => {
        if (!String(u).includes('/rpa/execute')) return false;
        const body = JSON.parse(String((i as RequestInit)?.body ?? '{}'));
        return body.tool_name === 'rpa_scroll';
      });
      expect(call).toBeTruthy();
    });
  });

  test('element picker mode resolves a selector and shows the chip with copy/dismiss', async () => {
    await renderWithActiveSession({ getSelectorResult: { success: true, output: '#save-btn', tool_name: 'rpa_get_selector' } });
    await userEvent.click(screen.getByRole('button', { name: /pick element/i }));
    expect(screen.getByRole('button', { name: /picker: on/i })).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('viewport-screenshot'), { clientX: 200, clientY: 100 });
    expect(await screen.findByText('#save-btn')).toBeInTheDocument();
    // Picker mode auto-exits after resolving
    expect(screen.getByRole('button', { name: /pick element/i })).toBeInTheDocument();
    await userEvent.click(screen.getByTitle(/copy selector/i));
    await userEvent.click(screen.getByTitle(/^dismiss$/i));
    expect(screen.queryByText('#save-btn')).not.toBeInTheDocument();
  });

  test('element picker falls back to coordinates toast when resolution fails', async () => {
    await renderWithActiveSession({ getSelectorResult: 'reject' });
    await userEvent.click(screen.getByRole('button', { name: /pick element/i }));
    fireEvent.click(screen.getByTestId('viewport-screenshot'), { clientX: 200, clientY: 100 });
    await waitFor(() => expect(screen.getByRole('button', { name: /pick element/i })).toBeInTheDocument());
    expect(screen.queryByTitle(/copy selector/i)).not.toBeInTheDocument();
  });

  test('clear action log button empties the log', async () => {
    await renderWithActiveSession();
    fireEvent.click(screen.getByTestId('viewport-screenshot'), { clientX: 100, clientY: 50 });
    await screen.findByText(/rpa_click/i);
    await userEvent.click(screen.getByLabelText(/clear log/i));
    expect(screen.getByText(/no actions yet/i)).toBeInTheDocument();
  });

  test('shows session health footer with age and action count', async () => {
    await renderWithActiveSession();
    expect(screen.getAllByText(/actions/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/age:/i)).toBeInTheDocument();
  });
});

describe('RpaLivePage — delete + takeover', () => {
  test('delete confirm modal: cancel does not close the session', async () => {
    const spy = await renderWithActiveSession();
    await userEvent.click(screen.getByLabelText(/close session/i));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(spy.mock.calls.some(([u, i]) => String(u).includes('/rpa/sessions/') && (i as RequestInit)?.method === 'DELETE')).toBe(false);
  });

  test('delete confirm modal: confirming closes the session', async () => {
    const spy = await renderWithActiveSession();
    await userEvent.click(screen.getByLabelText(/close session/i));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: /close session/i }));
    await waitFor(() => {
      expect(spy.mock.calls.some(([u, i]) => String(u).includes('/rpa/sessions/sess-001') && (i as RequestInit)?.method === 'DELETE')).toBe(true);
    });
    await waitFor(() => expect(screen.getByText(/no session selected/i)).toBeInTheDocument());
  });

  test('takeover modal submits a reason and closes', async () => {
    const spy = await renderWithActiveSession();
    await userEvent.click(screen.getByRole('button', { name: /takeover/i }));
    const reasonInput = screen.getByLabelText(/takeover reason/i);
    await userEvent.type(reasonInput, 'Need to log in manually');
    await userEvent.click(screen.getByRole('button', { name: /request takeover/i }));
    await waitFor(() => {
      const call = spy.mock.calls.find(([u, i]) => String(u).includes('/takeover') && (i as RequestInit)?.method === 'POST');
      expect(call).toBeTruthy();
      expect(JSON.parse(String((call![1] as RequestInit).body))).toEqual({ reason: 'Need to log in manually' });
    });
    await waitFor(() => expect(screen.queryByLabelText(/takeover reason/i)).not.toBeInTheDocument());
  });

  test('takeover modal cancel closes without submitting', async () => {
    await renderWithActiveSession();
    await userEvent.click(screen.getByRole('button', { name: /takeover/i }));
    await screen.findByLabelText(/takeover reason/i);
    await userEvent.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(screen.queryByLabelText(/takeover reason/i)).not.toBeInTheDocument();
  });
});

describe('RpaLivePage — tool console', () => {
  async function openToolConsole(opts?: Parameters<typeof mockFetch>[0]) {
    const spy = await renderWithActiveSession(opts);
    const toolConsoleBtn = screen.getAllByText(/tool console/i).find(el => el.closest('button'))!.closest('button')!;
    await userEvent.click(toolConsoleBtn);
    await waitFor(() => expect(screen.getByLabelText(/search rpa tools/i)).toBeInTheDocument());
    return spy;
  }

  test('expands and lists tools with risk badges', async () => {
    await openToolConsole();
    await waitFor(() => expect(screen.getByText('rpa_navigate')).toBeInTheDocument());
    expect(screen.getByText('rpa_click')).toBeInTheDocument();
  });

  test('search filters the tool list', async () => {
    await openToolConsole();
    await waitFor(() => expect(screen.getByText('rpa_navigate')).toBeInTheDocument());
    await userEvent.type(screen.getByLabelText(/search rpa tools/i), 'navigate');
    expect(screen.getByText('rpa_navigate')).toBeInTheDocument();
    expect(screen.queryByText('rpa_click')).not.toBeInTheDocument();
  });

  test('selecting a tool with a schema renders enum, boolean, and text fields; execute succeeds', async () => {
    const spy = await openToolConsole();
    await waitFor(() => expect(screen.getByText('rpa_navigate')).toBeInTheDocument());
    await userEvent.click(screen.getByText('rpa_navigate'));
    expect(screen.getByText(/navigate to a url/i)).toBeInTheDocument();

    // enum -> select field
    const waitUntilSelect = screen.getByDisplayValue('Select…');
    await userEvent.selectOptions(waitUntilSelect, 'load');
    // boolean -> checkbox
    const checkbox = screen.getByRole('checkbox');
    await userEvent.click(checkbox);
    // free text field
    const urlInput = screen.getByPlaceholderText(/target url/i);
    await userEvent.type(urlInput, 'https://example.com');

    await userEvent.click(screen.getByRole('button', { name: /^execute$/i }));
    await waitFor(() => {
      const call = spy.mock.calls.find(([u, i]) => {
        if (!String(u).includes('/rpa/execute')) return false;
        const body = JSON.parse(String((i as RequestInit)?.body ?? '{}'));
        return body.tool_name === 'rpa_navigate';
      });
      expect(call).toBeTruthy();
    });
    expect((await screen.findAllByText(/done/i)).length).toBeGreaterThan(0);
  });

  test('selecting a tool without a schema falls back to default params and shows an error result', async () => {
    await openToolConsole({ executeResult: { success: false, output: '', error: 'Element not found', tool_name: 'rpa_click' } });
    await waitFor(() => expect(screen.getByText('rpa_click')).toBeInTheDocument());
    await userEvent.click(screen.getByText('rpa_click'));
    expect(screen.getByPlaceholderText(/enter selector/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /^execute$/i }));
    expect(await screen.findByText(/element not found/i)).toBeInTheDocument();
  });
});
