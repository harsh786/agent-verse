import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { WorkflowBuilderPage } from './WorkflowBuilderPage';

vi.mock('@/stores/auth', () => ({ useAuthStore: (sel: any) => sel({ apiKey: 'test' }) }));

function Wrapper({ c }: { c: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter>{c}</MemoryRouter>
    </QueryClientProvider>
  );
}

describe('WorkflowBuilderPage', () => {
  it('renders page title', () => {
    render(<WorkflowBuilderPage />, { wrapper: ({ children }) => <Wrapper c={children} /> });
    expect(screen.getByText('Workflow Builder')).toBeDefined();
  });

  it('shows auto-generate button', () => {
    render(<WorkflowBuilderPage />, { wrapper: ({ children }) => <Wrapper c={children} /> });
    expect(screen.queryAllByText(/auto-generate/i).length).toBeGreaterThan(0);
  });

  it('shows start and end nodes by default', () => {
    render(<WorkflowBuilderPage />, { wrapper: ({ children }) => <Wrapper c={children} /> });
    expect(screen.getByDisplayValue('Start')).toBeDefined();
    expect(screen.getByDisplayValue('End')).toBeDefined();
  });
});
