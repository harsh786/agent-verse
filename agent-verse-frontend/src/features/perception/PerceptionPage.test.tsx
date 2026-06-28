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
        return new Response(
          JSON.stringify({
            playwright_available: true,
            vision_available: false,
            browser_actions: ['screenshot'],
            image_formats: ['png'],
          }),
          { status: 200 }
        );
      return new Response('{}', { status: 200 });
    });
    renderPage();
    expect(await screen.findByText(/browser \(playwright\)/i)).toBeInTheDocument();
    expect(screen.getByText(/vision llm/i)).toBeInTheDocument();
  });

  test('renders URL input and action buttons', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/perception/status'))
        return new Response(
          JSON.stringify({
            playwright_available: true,
            vision_available: true,
            browser_actions: [],
            image_formats: [],
          }),
          { status: 200 }
        );
      return new Response('{}', { status: 200 });
    });
    renderPage();
    expect(await screen.findByLabelText(/url/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /screenshot/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /analyze/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /extract text/i })).toBeInTheDocument();
  });

  test('screenshot renders the returned image', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/perception/status'))
        return new Response(
          JSON.stringify({ playwright_available: true, vision_available: true, browser_actions: [], image_formats: [] }),
          { status: 200 }
        );
      if (url.includes('/perception/screenshot') && init?.method === 'POST')
        return new Response(
          JSON.stringify({ success: true, url: 'http://x', screenshot_b64: 'QUJD', error: null }),
          { status: 200 }
        );
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await userEvent.type(await screen.findByLabelText(/url/i), 'http://x');
    await userEvent.click(screen.getByRole('button', { name: /screenshot/i }));
    await waitFor(() =>
      expect(screen.getByRole('img', { name: /screenshot/i })).toHaveAttribute(
        'src',
        expect.stringContaining('QUJD')
      )
    );
  });
});
