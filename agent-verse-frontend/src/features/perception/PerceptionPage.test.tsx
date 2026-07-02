import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { PerceptionPage } from './PerceptionPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <PerceptionPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const STATUS_OK = {
  playwright_available: true,
  vision_available: true,
  browser_actions: ['screenshot'],
  image_formats: ['png'],
};

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('PerceptionPage', () => {
  test('shows vision-provider status', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/perception/status'))
        return new Response(JSON.stringify(STATUS_OK), { status: 200 });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    expect(await screen.findByText(/browser \(playwright\)/i)).toBeInTheDocument();
    expect(screen.getByText(/vision llm/i)).toBeInTheDocument();
  });

  test('renders URL input and action buttons', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input).includes('/perception/status'))
        return new Response(JSON.stringify(STATUS_OK), { status: 200 });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    expect(await screen.findByLabelText(/^url$/i)).toBeInTheDocument();
    expect(screen.getByTestId('btn-screenshot')).toBeInTheDocument();
    expect(screen.getByTestId('btn-analyze')).toBeInTheDocument();
    expect(screen.getByTestId('btn-extract')).toBeInTheDocument();
  });

  test('screenshot renders the returned image', async () => {
    const B64 = 'QUJD';
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/perception/status'))
        return new Response(JSON.stringify(STATUS_OK), { status: 200 });
      if (url.includes('/perception/screenshot') && init?.method === 'POST')
        return new Response(
          JSON.stringify({ success: true, url: 'http://x', screenshot_b64: B64, error: null }),
          { status: 200 }
        );
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await userEvent.type(await screen.findByLabelText(/^url$/i), 'http://x');
    await userEvent.click(screen.getByTestId('btn-screenshot'));
    await waitFor(() =>
      expect(screen.getByRole('img', { name: /screenshot/i })).toHaveAttribute(
        'src',
        expect.stringContaining(B64)
      )
    );
  });

  test('shows install warning when playwright is unavailable', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input).includes('/perception/status'))
        return new Response(
          JSON.stringify({ playwright_available: false, vision_available: false, browser_actions: [], image_formats: [] }),
          { status: 200 }
        );
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByText(/browser \(playwright\)/i);
    expect(await screen.findByText(/playwright not installed/i)).toBeInTheDocument();
  });

  test('batch tab renders textarea and run button', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input).includes('/perception/status'))
        return new Response(JSON.stringify(STATUS_OK), { status: 200 });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByText(/browser \(playwright\)/i);
    await userEvent.click(screen.getByText('Batch Analysis'));
    expect(await screen.findByLabelText(/urls/i)).toBeInTheDocument();
    expect(screen.getByTestId('btn-run-batch')).toBeInTheDocument();
  });

  test('history tab shows empty state initially', async () => {
    localStorage.removeItem('perception_history_v1');
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input).includes('/perception/status'))
        return new Response(JSON.stringify(STATUS_OK), { status: 200 });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByText(/browser \(playwright\)/i);
    await userEvent.click(screen.getByText('History'));
    expect(await screen.findByText(/no analysis history yet/i)).toBeInTheDocument();
  });
});
