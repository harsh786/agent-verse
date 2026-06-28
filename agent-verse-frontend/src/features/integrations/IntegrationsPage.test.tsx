import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { IntegrationsPage } from './IntegrationsPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <IntegrationsPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
});
afterEach(() => vi.restoreAllMocks());

describe('IntegrationsPage', () => {
  test('shows the four providers with their endpoint paths', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }));
    renderPage();
    expect(screen.getByText('Slack')).toBeInTheDocument();
    expect(screen.getByText('Zapier')).toBeInTheDocument();
    expect(screen.getByText('Alertmanager')).toBeInTheDocument();
    expect(screen.getByText('Datadog')).toBeInTheDocument();
    expect(screen.getByText(/\/integrations\/slack\/commands/)).toBeInTheDocument();
    expect(screen.getByText(/\/integrations\/events\/datadog/)).toBeInTheDocument();
  });

  test('copy button writes the endpoint URL to the clipboard', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }));
    renderPage();
    await userEvent.click(screen.getAllByRole('button', { name: /copy endpoint/i })[0]);
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalled());
  });

  test('shows Zapier delivery from /integrations/zapier/goals', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/integrations/zapier/goals'))
        return new Response(
          JSON.stringify([{ goal_id: 'g1', goal: 'Resolve incident', status: 'complete' }]),
          { status: 200 }
        );
      return new Response('[]', { status: 200 });
    });
    renderPage();
    expect(await screen.findByText('Resolve incident')).toBeInTheDocument();
  });
});
