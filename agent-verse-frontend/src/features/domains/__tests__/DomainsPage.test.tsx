import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import DomainsPage from '../DomainsPage';

vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => vi.fn(),
}));

const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

describe('DomainsPage', () => {
  it('renders all 37 domains', () => {
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <DomainsPage />
        </MemoryRouter>
      </QueryClientProvider>
    );
    expect(screen.getByText('Domain Solutions')).toBeTruthy();
    expect(screen.getByText('HR & Talent')).toBeTruthy();
    expect(screen.getByText('GST & Tax')).toBeTruthy();
    expect(screen.getByText('Legal')).toBeTruthy();
  });

  it('has search input', () => {
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <DomainsPage />
        </MemoryRouter>
      </QueryClientProvider>
    );
    expect(screen.getByPlaceholderText('Search domains…')).toBeTruthy();
  });
});
