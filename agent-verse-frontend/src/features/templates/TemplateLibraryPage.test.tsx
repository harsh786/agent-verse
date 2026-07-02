import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
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

beforeEach(() => {
  sessionStorage.setItem('av_api_key', 'test-key');
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
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
    expect(await screen.findByText(/no templates found/i)).toBeInTheDocument();
  });

  test('shows domain filter pills', async () => {
    mockFetch([]);
    renderPage();
    await waitFor(() => expect(screen.getByRole('button', { name: /devops/i })).toBeInTheDocument());
  });

  test('search input filters templates', async () => {
    mockFetch([TEMPLATE]);
    renderPage();
    await screen.findByText('Deploy Service');
    await userEvent.type(screen.getByLabelText(/search templates/i), 'xyz-no-match');
    expect(screen.queryByText('Deploy Service')).not.toBeInTheDocument();
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
});
