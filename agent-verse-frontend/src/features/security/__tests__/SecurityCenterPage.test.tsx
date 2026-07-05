import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import SecurityCenterPage from '../SecurityCenterPage';

vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => vi.fn(),
}));

const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

describe('SecurityCenterPage', () => {
  it('renders all 6 tabs', () => {
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <SecurityCenterPage />
        </MemoryRouter>
      </QueryClientProvider>
    );
    expect(screen.getByText('Security Center')).toBeTruthy();
    expect(screen.getByText('Agent Identity')).toBeTruthy();
    expect(screen.getByText('Governance')).toBeTruthy();
    expect(screen.getByText('Guardrails')).toBeTruthy();
    expect(screen.getByText('Audit Trail')).toBeTruthy();
    expect(screen.getByText('Scopes & Roles')).toBeTruthy();
    expect(screen.getByText('Limits')).toBeTruthy();
  });

  it('shows security score', () => {
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <SecurityCenterPage />
        </MemoryRouter>
      </QueryClientProvider>
    );
    expect(screen.getByText('Security Score')).toBeTruthy();
  });
});
