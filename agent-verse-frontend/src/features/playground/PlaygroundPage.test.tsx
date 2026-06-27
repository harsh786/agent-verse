import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { PlaygroundPage } from './PlaygroundPage';

vi.mock('@/stores/auth', () => ({ useAuthStore: (sel: any) => sel({ apiKey: 'test' }) }));
vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})));

function Wrapper({ c }: { c: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter>{c}</MemoryRouter>
    </QueryClientProvider>
  );
}

describe('PlaygroundPage', () => {
  it('renders page title', () => {
    render(<PlaygroundPage />, { wrapper: ({ children }) => <Wrapper c={children} /> });
    expect(screen.getByText('Agent Playground')).toBeDefined();
  });

  it('renders goal input', () => {
    render(<PlaygroundPage />, { wrapper: ({ children }) => <Wrapper c={children} /> });
    expect(screen.getByPlaceholderText(/describe what the agent/i)).toBeDefined();
  });

  it('shows run button', () => {
    render(<PlaygroundPage />, { wrapper: ({ children }) => <Wrapper c={children} /> });
    expect(screen.getByText('Run Simulation')).toBeDefined();
  });
});
