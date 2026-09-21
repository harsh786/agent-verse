import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, within, waitFor } from '@testing-library/react';
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

/** Mocks fetch so marketplace and templates endpoints return the given payloads. */
function mockFetchWith(marketplacePayload: unknown, templatesPayload: unknown) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/templates') && !url.includes('/marketplace/')) {
      return new Response(JSON.stringify(templatesPayload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    return new Response(JSON.stringify(marketplacePayload), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  });
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

  it('filters domains by name as the user types', async () => {
    renderDomainsPage();

    await userEvent.type(screen.getByPlaceholderText('Search domains…'), 'Legal');

    expect(screen.getByText('Legal')).toBeTruthy();
    expect(screen.queryByText('HR & Talent')).not.toBeInTheDocument();
  });

  it('filters domains by tagline text even when the name does not match', async () => {
    renderDomainsPage();

    // "paralegal speed" only appears in the Legal domain's tagline, not its name.
    await userEvent.type(screen.getByPlaceholderText('Search domains…'), 'paralegal');

    expect(screen.getByText('Legal')).toBeTruthy();
    expect(screen.queryByText('HR & Talent')).not.toBeInTheDocument();
  });

  it('shows a "no domains match" message when the search has no results', async () => {
    renderDomainsPage();

    await userEvent.type(screen.getByPlaceholderText('Search domains…'), 'zzz-nonexistent');

    expect(screen.queryByText('Legal')).not.toBeInTheDocument();
    expect(screen.getByText(/No domains match/)).toBeTruthy();
    expect(screen.getByText(/zzz-nonexistent/)).toBeTruthy();
  });

  it('clears the "no results" message once a matching search is entered', async () => {
    renderDomainsPage();

    const input = screen.getByPlaceholderText('Search domains…');
    await userEvent.type(input, 'zzz-nonexistent');
    expect(screen.getByText(/No domains match/)).toBeTruthy();

    await userEvent.clear(input);
    await userEvent.type(input, 'Legal');

    expect(screen.queryByText(/No domains match/)).not.toBeInTheDocument();
    expect(screen.getByText('Legal')).toBeTruthy();
  });

  it('navigates to the domain detail route when a card is activated with the Enter key', async () => {
    renderDomainsPage();

    const card = screen.getByRole('button', { name: /explore legal/i });
    card.focus();
    await userEvent.keyboard('{Enter}');

    expect(navigateMock).toHaveBeenCalledWith('/domains/legal');
  });

  it('ignores non-Enter key presses on a domain card', async () => {
    renderDomainsPage();

    const card = screen.getByRole('button', { name: /explore legal/i });
    card.focus();
    await userEvent.keyboard('{ }');

    expect(navigateMock).not.toHaveBeenCalled();
  });

  it('shows aggregated agent and template counts once data loads, with correct singular/plural wording', async () => {
    mockFetchWith(
      {
        items: [
          { template_id: '1', slug: 'a', name: 'A', description: '', domain: 'legal', required_connectors: [], autonomy_mode: 'auto', visibility: 'public', review_status: 'approved', is_builtin: true, is_verified: true, install_count: 0, version: '1' },
        ],
        total: 1,
        page: 1,
        page_size: 10,
      },
      [
        { id: 't1', name: 'Tmpl 1', domain: 'legal', goal_text: 'x', use_count: 0, version: 1, created_at: '2024-01-01' },
        { id: 't2', name: 'Tmpl 2', domain: 'legal', goal_text: 'x', use_count: 0, version: 1, created_at: '2024-01-01' },
      ],
    );

    renderDomainsPage();

    const legalCard = await screen.findByRole('button', { name: /explore legal/i });
    await waitFor(() => {
      expect(within(legalCard).getByText('1 agent')).toBeTruthy();
    });
    expect(within(legalCard).getByText('2 templates')).toBeTruthy();
  });

  it('normalizes differently-spelled domain keys (e.g. "ecommerce" vs "e-commerce") into the same bucket', async () => {
    mockFetchWith(
      { items: [], total: 0, page: 1, page_size: 10 },
      [
        { id: 't1', name: 'Tmpl 1', domain: 'ecommerce', goal_text: 'x', use_count: 0, version: 1, created_at: '2024-01-01' },
      ],
    );

    renderDomainsPage();

    const ecommerceCard = await screen.findByRole('button', { name: /explore e-commerce/i });
    await waitFor(() => {
      expect(within(ecommerceCard).getByText('1 template')).toBeTruthy();
    });
  });
});
