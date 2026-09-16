import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { PerceptionPage } from './PerceptionPage';

// Companion suite to PerceptionPage.test.tsx — targets branches not covered
// there: URL validation, advanced options, question presets, analyze/extract
// success + failure paths, screenshot+analyze combined flow, goal creation,
// downloads, copy-to-clipboard, batch results (success/failure), and history
// tab with populated entries.

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

const STATUS_OK = {
  playwright_available: true,
  vision_available: true,
  browser_actions: ['screenshot'],
  image_formats: ['png'],
};

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

  // jsdom doesn't implement these — stub for download / copy interactions.
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
  Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:mock-url'), revokeObjectURL: vi.fn() });
});
afterEach(() => vi.restoreAllMocks());

describe('PerceptionPage branches', () => {
  test('shows a validation message for a non-http URL', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) =>
      String(input).includes('/perception/status') ? json(STATUS_OK) : json({})
    );
    renderPage();
    await userEvent.type(await screen.findByLabelText(/^url$/i), 'not-a-url');
    expect(await screen.findByText(/must start with http/i)).toBeInTheDocument();
    expect(screen.getByTestId('btn-screenshot')).toBeDisabled();
  });

  test('selecting a question preset updates the question field', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) =>
      String(input).includes('/perception/status') ? json(STATUS_OK) : json({})
    );
    renderPage();
    const select = await screen.findByLabelText(/question presets/i);
    await userEvent.selectOptions(select, 'What are the key UI elements and their functions?');
    expect(screen.getByLabelText(/analysis question/i)).toHaveValue('What are the key UI elements and their functions?');
  });

  test('advanced options toggle reveals full-page checkbox and selector input', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) =>
      String(input).includes('/perception/status') ? json(STATUS_OK) : json({})
    );
    renderPage();
    await screen.findByLabelText(/^url$/i);
    expect(screen.queryByLabelText(/full-page screenshot/i)).not.toBeInTheDocument();
    await userEvent.click(screen.getByText(/advanced options/i));
    expect(screen.getByLabelText(/full-page screenshot/i)).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText(/full-page screenshot/i));
    expect(screen.getByLabelText(/full-page screenshot/i)).toBeChecked();
    await userEvent.clear(screen.getByLabelText(/css selector/i));
    await userEvent.type(screen.getByLabelText(/css selector/i), '#main');
    expect(screen.getByLabelText(/css selector/i)).toHaveValue('#main');
    // Collapse again
    await userEvent.click(screen.getByText(/advanced options/i));
    expect(screen.queryByLabelText(/full-page screenshot/i)).not.toBeInTheDocument();
  });

  test('screenshot failure shows an error toast and does not render an image', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/perception/status')) return json(STATUS_OK);
      if (url.includes('/perception/screenshot') && (init?.method === 'POST'))
        return json({ success: false, url: 'http://x', screenshot_b64: '', error: 'Blocked by robots.txt' });
      return json({});
    });
    renderPage();
    await userEvent.type(await screen.findByLabelText(/^url$/i), 'http://x');
    await userEvent.click(screen.getByTestId('btn-screenshot'));
    await waitFor(() => expect(screen.queryByRole('img', { name: /screenshot/i })).not.toBeInTheDocument());
  });

  test('analyze without a prior screenshot runs the combined screenshot+analyze flow', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/perception/status')) return json(STATUS_OK);
      if (url.includes('/perception/screenshot') && init?.method === 'POST')
        return json({ success: true, url: 'http://x', screenshot_b64: 'QUJD', error: null });
      if (url.includes('/perception/analyze') && init?.method === 'POST')
        return json({ analysis: 'This page sells widgets.', question: 'q', screenshot_provided: true });
      return json({});
    });
    renderPage();
    await userEvent.type(await screen.findByLabelText(/^url$/i), 'http://x');
    await userEvent.click(screen.getByTestId('btn-analyze'));
    expect(await screen.findByText('This page sells widgets.')).toBeInTheDocument();
  });

  test('analyze after a screenshot exists uses the screenshot-only analyze mutation', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/perception/status')) return json(STATUS_OK);
      if (url.includes('/perception/screenshot') && init?.method === 'POST')
        return json({ success: true, url: 'http://x', screenshot_b64: 'QUJD', error: null });
      if (url.includes('/perception/analyze') && init?.method === 'POST')
        return json({ analysis: 'Analyzed from cached screenshot.', question: 'q', screenshot_provided: true });
      return json({});
    });
    renderPage();
    await userEvent.type(await screen.findByLabelText(/^url$/i), 'http://x');
    await userEvent.click(screen.getByTestId('btn-screenshot'));
    await waitFor(() => expect(screen.getByRole('img', { name: /screenshot/i })).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('btn-analyze'));
    expect(await screen.findByText('Analyzed from cached screenshot.')).toBeInTheDocument();
  });

  test('extract failure surfaces the error and does not render extracted text', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/perception/status')) return json(STATUS_OK);
      if (url.includes('/perception/extract') && init?.method === 'POST')
        return json({ success: false, url: 'http://x', selector: 'body', text: '', char_count: 0, error: 'Timed out' });
      return json({});
    });
    renderPage();
    await userEvent.type(await screen.findByLabelText(/^url$/i), 'http://x');
    await userEvent.click(screen.getByTestId('btn-extract'));
    await waitFor(() => expect(screen.queryByText(/chars · selector/i)).not.toBeInTheDocument());
  });

  test('extract success renders the extracted text panel with copy button', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/perception/status')) return json(STATUS_OK);
      if (url.includes('/perception/extract') && init?.method === 'POST')
        return json({ success: true, url: 'http://x', selector: 'body', text: 'Hello world', char_count: 11, error: null });
      return json({});
    });
    renderPage();
    await userEvent.type(await screen.findByLabelText(/^url$/i), 'http://x');
    await userEvent.click(screen.getByTestId('btn-extract'));
    expect(await screen.findByText('Hello world')).toBeInTheDocument();
    await userEvent.click(screen.getByText('Copy'));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('Hello world');
  });

  test('create-goal and export-analysis actions appear once an analysis exists', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/perception/status')) return json(STATUS_OK);
      if (url.includes('/perception/screenshot') && init?.method === 'POST')
        return json({ success: true, url: 'http://x', screenshot_b64: 'QUJD', error: null });
      if (url.includes('/perception/analyze') && init?.method === 'POST')
        return json({ analysis: 'Great page.', question: 'q', screenshot_provided: true });
      return json({});
    });
    renderPage();
    await userEvent.type(await screen.findByLabelText(/^url$/i), 'http://x');
    await userEvent.click(screen.getByTestId('btn-analyze'));
    expect(await screen.findByText('Great page.')).toBeInTheDocument();
    expect(screen.getByText(/create goal from this page/i)).toBeInTheDocument();
    await userEvent.click(screen.getByText(/export analysis/i));
    expect(URL.createObjectURL).toHaveBeenCalled();
  });

  test('screenshot zoom toggle switches between "Full size" and "Fit"', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/perception/status')) return json(STATUS_OK);
      if (url.includes('/perception/screenshot') && init?.method === 'POST')
        return json({ success: true, url: 'http://x', screenshot_b64: 'QUJD', error: null });
      return json({});
    });
    renderPage();
    await userEvent.type(await screen.findByLabelText(/^url$/i), 'http://x');
    await userEvent.click(screen.getByTestId('btn-screenshot'));
    await waitFor(() => expect(screen.getByRole('img', { name: /screenshot/i })).toBeInTheDocument());
    expect(screen.getByText(/full size/i)).toBeInTheDocument();
    await userEvent.click(screen.getByText(/full size/i));
    expect(screen.getByText(/^fit$/i)).toBeInTheDocument();
  });

  test('download screenshot button triggers an anchor click', async () => {
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/perception/status')) return json(STATUS_OK);
      if (url.includes('/perception/screenshot') && init?.method === 'POST')
        return json({ success: true, url: 'http://x', screenshot_b64: 'QUJD', error: null });
      return json({});
    });
    renderPage();
    await userEvent.type(await screen.findByLabelText(/^url$/i), 'http://x');
    await userEvent.click(screen.getByTestId('btn-screenshot'));
    await waitFor(() => expect(screen.getByRole('img', { name: /screenshot/i })).toBeInTheDocument());
    await userEvent.click(screen.getByText(/save/i));
    expect(clickSpy).toHaveBeenCalled();
  });

  test('batch tab: mixed success/failure results render with copy + clear', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/perception/status')) return json(STATUS_OK);
      if (url.includes('/perception/batch-analyze') && init?.method === 'POST')
        return json({
          total: 2,
          succeeded: 1,
          results: [
            { url: 'http://ok.com', success: true, analysis: 'Looks fine', screenshot_b64: 'QUJD', error: null },
            { url: 'http://bad.com', success: false, analysis: '', screenshot_b64: '', error: 'DNS failure' },
          ],
        });
      return json({});
    });
    renderPage();
    await screen.findByLabelText(/^url$/i);
    await userEvent.click(screen.getByText('Batch Analysis'));
    await userEvent.type(await screen.findByLabelText(/urls/i), 'http://ok.com\nhttp://bad.com');
    expect(await screen.findByText(/2 valid urls detected/i)).toBeInTheDocument();
    await userEvent.click(screen.getByTestId('btn-run-batch'));
    expect(await screen.findByText(/1\/2 succeeded/i)).toBeInTheDocument();
    expect(screen.getByText('Looks fine')).toBeInTheDocument();
    expect(screen.getByText('DNS failure')).toBeInTheDocument();
    await userEvent.click(screen.getByText(/clear/i));
    expect(screen.queryByText(/succeeded/i)).not.toBeInTheDocument();
  });

  test('batch run button is disabled with zero or more than ten URLs', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) =>
      String(input).includes('/perception/status') ? json(STATUS_OK) : json({})
    );
    renderPage();
    await screen.findByLabelText(/^url$/i);
    await userEvent.click(screen.getByText('Batch Analysis'));
    expect(screen.getByTestId('btn-run-batch')).toBeDisabled();
    const manyUrls = Array.from({ length: 11 }, (_, i) => `http://site${i}.com`).join('\n');
    await userEvent.type(await screen.findByLabelText(/urls/i), manyUrls);
    expect(screen.getByTestId('btn-run-batch')).toBeDisabled();
  });

  test('history tab renders populated entries with screenshot and extracted text, then clears', async () => {
    localStorage.setItem(
      'perception_history_v1',
      JSON.stringify([
        {
          id: 'h1',
          url: 'http://history.com',
          question: 'What is this?',
          screenshot_b64: 'QUJD',
          analysis: 'A history entry analysis.',
          extracted: { text: 'extracted body text', charCount: 20, selector: 'body' },
          timestamp: Date.now(),
        },
      ])
    );
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) =>
      String(input).includes('/perception/status') ? json(STATUS_OK) : json({})
    );
    renderPage();
    await screen.findByLabelText(/^url$/i);
    await userEvent.click(screen.getByText('History'));
    expect(await screen.findByText('http://history.com')).toBeInTheDocument();
    expect(screen.getByText('A history entry analysis.')).toBeInTheDocument();
    expect(screen.getByText(/extracted \(20 chars\)/i)).toBeInTheDocument();
    await userEvent.click(screen.getByText(/clear all/i));
    expect(await screen.findByText(/no analysis history yet/i)).toBeInTheDocument();
  });

  test('playwright unavailable disables single-tab and batch-tab action buttons', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) =>
      String(input).includes('/perception/status')
        ? json({ playwright_available: false, vision_available: false, browser_actions: [], image_formats: [] })
        : json({})
    );
    renderPage();
    await userEvent.type(await screen.findByLabelText(/^url$/i), 'http://x');
    expect(screen.getByTestId('btn-screenshot')).toBeDisabled();
    expect(screen.getByTestId('btn-analyze')).toBeDisabled();
    expect(screen.getByTestId('btn-extract')).toBeDisabled();
  });
});
