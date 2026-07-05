import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StatusPage } from './StatusPage';

const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
  ok: true,
  json: async () => ({
    status: 'operational',
    components: {
      postgres: { status: 'operational', latency_ms: 2.4 },
      redis: { status: 'operational', latency_ms: 0.8 },
    },
    timestamp: Date.now() / 1000,
  }),
}));

describe('StatusPage', () => {
  it('renders the status page heading', () => {
    render(<QueryClientProvider client={qc}><StatusPage /></QueryClientProvider>);
    expect(screen.getByText('AgentVerse Status')).toBeInTheDocument();
  });

  it('has a refresh button', () => {
    render(<QueryClientProvider client={qc}><StatusPage /></QueryClientProvider>);
    expect(screen.getByRole('button', { name: /refresh/i })).toBeInTheDocument();
  });
});
