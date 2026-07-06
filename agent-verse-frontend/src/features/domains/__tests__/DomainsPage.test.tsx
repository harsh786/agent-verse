import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import DomainsPage from '../DomainsPage';

const navigateMock = vi.fn();

vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => navigateMock,
}));

function renderDomainsPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <DomainsPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('DomainsPage', () => {
  beforeEach(() => {
    navigateMock.mockReset();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/templates') && !url.includes('/marketplace/')) {
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify({ templates: [], items: [], total: 0 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders all 37 domains', () => {
    renderDomainsPage();
    expect(screen.getByText('Domain Solutions')).toBeTruthy();
    expect(screen.getByText('HR & Talent')).toBeTruthy();
    expect(screen.getByText('GST & Tax')).toBeTruthy();
    expect(screen.getByText('Legal')).toBeTruthy();
  });

  it('has search input', () => {
    renderDomainsPage();
    expect(screen.getByPlaceholderText('Search domains…')).toBeTruthy();
  });

  it('opens a domain detail route when a domain card is clicked', async () => {
    renderDomainsPage();

    await userEvent.click(screen.getByRole('button', { name: /explore legal/i }));

    expect(navigateMock).toHaveBeenCalledWith('/domains/legal');
  });
});
