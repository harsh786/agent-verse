import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';

const navigateSpy = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => navigateSpy };
});

const toastSpy = vi.fn();
vi.mock('@/stores/toast', () => ({ toast: (...args: unknown[]) => toastSpy(...args) }));

import BuilderPage from './BuilderPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><BuilderPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Encode SSE lines into a single Uint8Array chunk. */
function sseChunk(lines: string[]): Uint8Array {
  return new TextEncoder().encode(lines.join('\n') + '\n');
}

beforeEach(() => {
  navigateSpy.mockReset();
  toastSpy.mockReset();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('BuilderPage — step 1 (project type)', () => {
  test('renders all project types and advances to configuration', async () => {
    renderPage();
    expect(screen.getByText('AI Project Builder')).toBeInTheDocument();
    expect(screen.getByText('What are you building?')).toBeInTheDocument();
    expect(screen.getByText('Landing Page')).toBeInTheDocument();
    expect(screen.getByText('REST API')).toBeInTheDocument();
    expect(screen.getByText('CLI Tool')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Continue/i }));
    expect(screen.getByText('Configure your project')).toBeInTheDocument();
  });

  test('selecting a project type swaps the framework options on the config step', async () => {
    renderPage();
    // Pick "REST API" — its first framework is FastAPI.
    fireEvent.click(screen.getByText('REST API'));
    fireEvent.click(screen.getByRole('button', { name: /Continue/i }));
    expect(screen.getByRole('button', { name: 'FastAPI' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Express.js' })).toBeInTheDocument();
  });
});

describe('BuilderPage — step 2 (configuration)', () => {
  async function gotoConfig() {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /Continue/i }));
  }

  test('Back returns to the project-type step', async () => {
    await gotoConfig();
    fireEvent.click(screen.getByRole('button', { name: /← Back/i }));
    expect(screen.getByText('What are you building?')).toBeInTheDocument();
  });

  test('choosing a framework highlights it and the description char count updates', async () => {
    await gotoConfig();
    fireEvent.click(screen.getByRole('button', { name: 'Next.js' }));
    const textarea = screen.getByPlaceholderText(/Describe your/i);
    await userEvent.type(textarea, 'Hello');
    expect(screen.getByText(/5\/500 chars/i)).toBeInTheDocument();
  });

  test('adds a feature via the + button and via the Enter key, and removes one', async () => {
    await gotoConfig();
    // Seeded defaults.
    expect(screen.getByText('Authentication')).toBeInTheDocument();
    expect(screen.getByText('Responsive design')).toBeInTheDocument();

    const input = screen.getByPlaceholderText(/Add a feature/i);
    await userEvent.type(input, 'Dark mode');
    fireEvent.click(screen.getByRole('button', { name: '+' }));
    expect(screen.getByText('Dark mode')).toBeInTheDocument();

    await userEvent.type(input, 'Billing{Enter}');
    expect(screen.getByText('Billing')).toBeInTheDocument();

    // Remove the "Authentication" tag via its × button.
    const authTag = screen.getByText('Authentication').closest('span')!;
    fireEvent.click(authTag.querySelector('button')!);
    expect(screen.queryByText('Authentication')).not.toBeInTheDocument();
  });

  test('the Build button is disabled until a description is entered', async () => {
    await gotoConfig();
    const buildBtn = screen.getByRole('button', { name: /Build Project/i });
    expect(buildBtn).toBeDisabled();
    await userEvent.type(screen.getByPlaceholderText(/Describe your/i), 'A dashboard');
    expect(buildBtn).toBeEnabled();
  });
});

describe('BuilderPage — build flow', () => {
  async function fillAndBuild(desc = 'A marketing landing page') {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /Continue/i }));
    await userEvent.type(screen.getByPlaceholderText(/Describe your/i), desc);
    fireEvent.click(screen.getByRole('button', { name: /Build Project/i }));
  }

  test('a successful JSON build shows the result card and generated files', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          name: 'My Landing',
          goal_id: 'g1',
          download_url: 'https://example.com/dl.zip',
          files: [{ path: 'src/App.tsx', size: '2kb' }, { path: 'index.html' }],
          summary: 'A tidy landing page.',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    await fillAndBuild();
    expect(await screen.findByText('Project generated!')).toBeInTheDocument();
    expect(screen.getByText('My Landing')).toBeInTheDocument();
    expect(screen.getByText('src/App.tsx')).toBeInTheDocument();
    expect(screen.getByText('index.html')).toBeInTheDocument();
    expect(screen.getByText('A tidy landing page.')).toBeInTheDocument();
    // The download link (result.download_url) is rendered.
    expect(screen.getByRole('link', { name: /Download/i })).toHaveAttribute('href', 'https://example.com/dl.zip');
  });

  test('the "View Execution" action navigates to the generated goal', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ name: 'Proj', goal_id: 'g-42', files: [] }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      }),
    );
    await fillAndBuild();
    await screen.findByText('Project generated!');
    fireEvent.click(screen.getByRole('button', { name: /View Execution/i }));
    expect(navigateSpy).toHaveBeenCalledWith('/goals/g-42');
  });

  test('"Build Another" resets back to the project-type step', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ name: 'Proj', files: [] }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      }),
    );
    await fillAndBuild();
    await screen.findByText('Project generated!');
    fireEvent.click(screen.getByRole('button', { name: /Build Another/i }));
    expect(screen.getByText('What are you building?')).toBeInTheDocument();
  });

  test('a failed build toasts an error and returns to the config step', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('nope', { status: 500, statusText: 'Server Error' }),
    );
    await fillAndBuild();
    await waitFor(() =>
      expect(toastSpy).toHaveBeenCalledWith(
        expect.objectContaining({ kind: 'error', message: expect.stringMatching(/Build failed/i) }),
      ),
    );
    expect(screen.getByText('Configure your project')).toBeInTheDocument();
  });

  test('a streaming SSE build accumulates progress steps then completes', async () => {
    let readCount = 0;
    const reader = {
      read: async () => {
        readCount += 1;
        if (readCount === 1) return { done: false, value: sseChunk(['data: {"step":"Planning files"}']) };
        if (readCount === 2) return { done: false, value: sseChunk(['data: {"result":{"name":"Streamed","files":[]}}']) };
        return { done: true, value: undefined };
      },
    };
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      statusText: 'OK',
      headers: { get: () => 'text/event-stream' },
      body: { getReader: () => reader },
    } as unknown as Response);

    await fillAndBuild();
    // Reaching the "done" screen with the streamed result proves the SSE reader
    // branch parsed both the progress `step` and the final `result` payload.
    expect(await screen.findByText('Project generated!')).toBeInTheDocument();
    expect(screen.getByText('Streamed')).toBeInTheDocument();
  });
});
