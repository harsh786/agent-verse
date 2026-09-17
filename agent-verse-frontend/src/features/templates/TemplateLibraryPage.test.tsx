import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { TemplateLibraryPage } from './TemplateLibraryPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <TemplateLibraryPage />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

const TEMPLATE = {
  id: 't1', name: 'Deploy Service', description: 'Deploy a microservice', goal_text: 'Deploy {{service}} to {{env}}',
  domain: 'devops', parameters: [{ name: 'service', description: '', required: true }, { name: 'env', description: '', required: true }],
  use_count: 5, version: 1, created_at: new Date().toISOString(),
};

function mockFetch(templates = [TEMPLATE]) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    if (String(input).includes('/templates'))
      return new Response(JSON.stringify(templates), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('[]', { status: 200 });
  });
}

function mockFetchByMethod(handlers: {
  list?: unknown[];
  create?: { status: number; body: unknown };
  update?: { status: number; body: unknown };
  delete?: { status: number; body: unknown };
}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/templates') && method === 'GET') {
      return new Response(JSON.stringify(handlers.list ?? []), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    if (method === 'POST' && !url.includes('/instantiate')) {
      const h = handlers.create ?? { status: 201, body: TEMPLATE };
      return new Response(JSON.stringify(h.body), { status: h.status, headers: { 'Content-Type': 'application/json' } });
    }
    if (method === 'PUT') {
      const h = handlers.update ?? { status: 200, body: {} };
      return new Response(JSON.stringify(h.body), { status: h.status, headers: { 'Content-Type': 'application/json' } });
    }
    if (method === 'DELETE') {
      const h = handlers.delete ?? { status: 204, body: undefined };
      return new Response(h.body === undefined ? null : JSON.stringify(h.body), { status: h.status });
    }
    return new Response('[]', { status: 200 });
  });
}

beforeEach(() => {
  sessionStorage.setItem('av_api_key', 'test-key');
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
  useToastStore.setState({ toasts: [] });
});
afterEach(() => vi.restoreAllMocks());

describe('TemplateLibraryPage', () => {
  test('renders Template Library heading', () => {
    mockFetch([]);
    renderPage();
    expect(screen.getByRole('heading', { name: /template library/i })).toBeInTheDocument();
  });

  test('shows New Template button', () => {
    mockFetch([]);
    renderPage();
    expect(screen.getByRole('button', { name: /new template/i })).toBeInTheDocument();
  });

  test('lists templates', async () => {
    mockFetch([TEMPLATE]);
    renderPage();
    expect(await screen.findByText('Deploy Service')).toBeInTheDocument();
  });

  test('shows empty state when no templates', async () => {
    mockFetch([]);
    renderPage();
    // The empty state renders when filtered.length === 0
    expect(await screen.findByText(/create your first template/i)).toBeInTheDocument();
  });

  test('shows domain filter pills', async () => {
    mockFetch([]);
    renderPage();
    await waitFor(() => expect(screen.getByRole('button', { name: /devops/i })).toBeInTheDocument());
  });

  test('search input filters templates via server', async () => {
    // Since search is now server-side, the second fetch (with search param) returns empty
    let fetchCallCount = 0;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      fetchCallCount++;
      const url = String(input);
      if (url.includes('/templates')) {
        // First call (no search) returns a template; subsequent calls (with search) return empty
        const hasSearch = url.includes('search=') || url.includes('q=');
        const body = hasSearch ? '[]' : JSON.stringify([TEMPLATE]);
        return new Response(body, { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await screen.findByText('Deploy Service');
    const searchInput = screen.getByLabelText(/search templates/i);
    await userEvent.clear(searchInput);
    await userEvent.type(searchInput, 'xyz-no-match');
    // After debounce, query with search param returns empty — template disappears
    await waitFor(() => expect(screen.queryByText('Deploy Service')).not.toBeInTheDocument(), { timeout: 3000 });
  });

  test('New Template button opens create modal', async () => {
    mockFetch([]);
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /new template/i }));
    expect(screen.getByRole('heading', { name: /new template/i })).toBeInTheDocument();
  });

  test('template card has Edit and Delete buttons', async () => {
    mockFetch([TEMPLATE]);
    renderPage();
    await screen.findByText('Deploy Service');
    expect(screen.getByRole('button', { name: /edit template/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /delete template/i })).toBeInTheDocument();
  });

  test('Use template opens the instantiator modal and Close button closes it', async () => {
    mockFetch([TEMPLATE]);
    renderPage();
    await screen.findByText('Deploy Service');
    await userEvent.click(screen.getByRole('button', { name: /use template: deploy service/i }));
    expect(screen.getByRole('heading', { name: 'Deploy Service', level: 2 })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('heading', { name: 'Deploy Service', level: 2 })).not.toBeInTheDocument();
  });

  test('"Create your first template" link in empty state opens create modal', async () => {
    mockFetch([]);
    renderPage();
    await screen.findByText(/no templates found/i);
    await userEvent.click(screen.getByRole('button', { name: /create your first template/i }));
    expect(screen.getByRole('heading', { name: 'New Template' })).toBeInTheDocument();
  });

  test('create modal: filling fields and submitting succeeds', async () => {
    mockFetchByMethod({ list: [], create: { status: 201, body: TEMPLATE } });
    renderPage();
    await screen.findByText(/no templates found/i);
    await userEvent.click(screen.getByRole('button', { name: /new template/i }));

    const heading = screen.getByRole('heading', { name: 'New Template' });
    const modal = heading.closest('.space-y-4') as HTMLElement;

    await userEvent.type(within(modal).getByLabelText('Name'), 'My Template');
    await userEvent.type(within(modal).getByLabelText('Description'), 'A description');
    await userEvent.type(within(modal).getByLabelText(/goal template/i), 'Do {{thing}}');
    await userEvent.selectOptions(within(modal).getByLabelText('Domain'), 'engineering');

    await userEvent.click(within(modal).getByRole('button', { name: 'New Template' }));

    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.kind === 'success' && /created/i.test(t.message))).toBe(true)
    );
    expect(screen.queryByRole('heading', { name: 'New Template' })).not.toBeInTheDocument();
  });

  test('create modal: server error surfaces an error toast', async () => {
    mockFetchByMethod({ list: [], create: { status: 400, body: { error: { message: 'Invalid template' } } } });
    renderPage();
    await screen.findByText(/no templates found/i);
    await userEvent.click(screen.getByRole('button', { name: /new template/i }));

    const heading = screen.getByRole('heading', { name: 'New Template' });
    const modal = heading.closest('.space-y-4') as HTMLElement;
    await userEvent.type(within(modal).getByLabelText('Name'), 'My Template');
    await userEvent.type(within(modal).getByLabelText(/goal template/i), 'Do {{thing}}');
    await userEvent.click(within(modal).getByRole('button', { name: 'New Template' }));

    await waitFor(() => expect(useToastStore.getState().toasts.some((t) => t.kind === 'error')).toBe(true));
    // Modal stays open on error
    expect(screen.getByRole('heading', { name: 'New Template' })).toBeInTheDocument();
  });

  test('edit modal: pre-fills fields and successfully updates', async () => {
    mockFetchByMethod({ list: [TEMPLATE], update: { status: 200, body: {} } });
    renderPage();
    await screen.findByText('Deploy Service');
    await userEvent.click(screen.getByRole('button', { name: /edit template: deploy service/i }));

    const heading = screen.getByRole('heading', { name: 'Edit Template' });
    const modal = heading.closest('.space-y-4') as HTMLElement;
    expect(within(modal).getByLabelText('Name')).toHaveValue('Deploy Service');
    expect(within(modal).getByLabelText('Description')).toHaveValue('Deploy a microservice');
    expect(within(modal).getByLabelText(/goal template/i)).toHaveValue('Deploy {{service}} to {{env}}');
    expect(within(modal).getByLabelText('Domain')).toHaveValue('devops');

    await userEvent.clear(within(modal).getByLabelText('Name'));
    await userEvent.type(within(modal).getByLabelText('Name'), 'Deploy Service v2');
    await userEvent.click(within(modal).getByRole('button', { name: 'Edit Template' }));

    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.kind === 'success' && /updated/i.test(t.message))).toBe(true)
    );
    expect(screen.queryByRole('heading', { name: 'Edit Template' })).not.toBeInTheDocument();
  });

  test('edit modal: server error surfaces an error toast and keeps modal open', async () => {
    mockFetchByMethod({ list: [TEMPLATE], update: { status: 500, body: { error: { message: 'boom' } } } });
    renderPage();
    await screen.findByText('Deploy Service');
    await userEvent.click(screen.getByRole('button', { name: /edit template: deploy service/i }));
    const heading = screen.getByRole('heading', { name: 'Edit Template' });
    const modal = heading.closest('.space-y-4') as HTMLElement;
    await userEvent.click(within(modal).getByRole('button', { name: 'Edit Template' }));

    await waitFor(() => expect(useToastStore.getState().toasts.some((t) => t.kind === 'error')).toBe(true));
    expect(screen.getByRole('heading', { name: 'Edit Template' })).toBeInTheDocument();
  });

  test('edit modal: Close (X) button dismisses without saving', async () => {
    mockFetch([TEMPLATE]);
    renderPage();
    await screen.findByText('Deploy Service');
    await userEvent.click(screen.getByRole('button', { name: /edit template: deploy service/i }));
    expect(screen.getByRole('heading', { name: 'Edit Template' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('heading', { name: 'Edit Template' })).not.toBeInTheDocument();
  });

  test('delete flow: cancel dismisses without deleting, confirm deletes with success toast', async () => {
    mockFetchByMethod({ list: [TEMPLATE], delete: { status: 204, body: undefined } });
    renderPage();
    await screen.findByText('Deploy Service');

    // Cancel path
    await userEvent.click(screen.getByRole('button', { name: /delete template: deploy service/i }));
    expect(screen.getByRole('heading', { name: /delete template\?/i })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('heading', { name: /delete template\?/i })).not.toBeInTheDocument();

    // Confirm path
    await userEvent.click(screen.getByRole('button', { name: /delete template: deploy service/i }));
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.kind === 'success' && /deleted/i.test(t.message))).toBe(true)
    );
    expect(screen.queryByRole('heading', { name: /delete template\?/i })).not.toBeInTheDocument();
  });

  test('delete flow: server error surfaces an error toast', async () => {
    mockFetchByMethod({ list: [TEMPLATE], delete: { status: 500, body: { error: { message: 'nope' } } } });
    renderPage();
    await screen.findByText('Deploy Service');
    await userEvent.click(screen.getByRole('button', { name: /delete template: deploy service/i }));
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(useToastStore.getState().toasts.some((t) => t.kind === 'error')).toBe(true));
  });

  test('domain filter pill toggles on and off', async () => {
    mockFetch([TEMPLATE]);
    renderPage();
    await screen.findByText('Deploy Service');

    const allButton = screen.getByRole('button', { name: 'All' });
    const devopsButton = screen.getByRole('button', { name: 'devops' });
    expect(allButton).toHaveClass('bg-primary');
    expect(devopsButton).not.toHaveClass('bg-primary');

    await userEvent.click(devopsButton);
    expect(devopsButton).toHaveClass('bg-primary');
    expect(allButton).not.toHaveClass('bg-primary');

    // Clicking the already-active pill toggles it back off
    await userEvent.click(devopsButton);
    expect(allButton).toHaveClass('bg-primary');
    expect(devopsButton).not.toHaveClass('bg-primary');
  });

  test('"All" pill resets an active domain filter', async () => {
    mockFetch([TEMPLATE]);
    renderPage();
    await screen.findByText('Deploy Service');
    await userEvent.click(screen.getByRole('button', { name: 'devops' }));
    expect(screen.getByRole('button', { name: 'devops' })).toHaveClass('bg-primary');
    await userEvent.click(screen.getByRole('button', { name: 'All' }));
    expect(screen.getByRole('button', { name: 'All' })).toHaveClass('bg-primary');
    expect(screen.getByRole('button', { name: 'devops' })).not.toHaveClass('bg-primary');
  });
});
