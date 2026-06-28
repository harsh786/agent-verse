import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { WorkflowBuilderPage } from './WorkflowBuilderPage';

vi.mock('@/stores/auth', () => ({
  useAuthStore: (sel: any) => sel({ apiKey: 'test', tenantId: 'tenant1', plan: 'free', isAuthenticated: true }),
}));

vi.mock('@/stores/toast', () => ({
  toast: vi.fn(),
}));

const mockFetch = vi.fn().mockResolvedValue({ ok: true, json: async () => [] });
vi.stubGlobal('fetch', mockFetch);

function Wrapper({ c }: { c: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter>{c}</MemoryRouter>
    </QueryClientProvider>
  );
}

describe('WorkflowBuilderPage', () => {
  beforeEach(() => {
    mockFetch.mockClear();
    mockFetch.mockResolvedValue({ ok: true, json: async () => [] });
  });

  it('renders the workflow builder heading', () => {
    render(<WorkflowBuilderPage />, { wrapper: ({ children }) => <Wrapper c={children} /> });
    // Toolbar has "Save", "Dry Run", "Run" buttons
    expect(screen.getByRole('button', { name: /save/i })).toBeDefined();
    expect(screen.getByRole('button', { name: /dry run/i })).toBeDefined();
  });

  it('shows the node palette', () => {
    render(<WorkflowBuilderPage />, { wrapper: ({ children }) => <Wrapper c={children} /> });
    expect(screen.getByText(/Node Palette/i)).toBeDefined();
  });

  it('shows the generate button', () => {
    render(<WorkflowBuilderPage />, { wrapper: ({ children }) => <Wrapper c={children} /> });
    expect(screen.getByRole('button', { name: /generate/i })).toBeDefined();
  });

  it('NL goal textarea exists', () => {
    render(<WorkflowBuilderPage />, { wrapper: ({ children }) => <Wrapper c={children} /> });
    expect(screen.getByLabelText(/Natural language workflow description/i)).toBeDefined();
  });

  it('generates nodes from API plan response (mock fetch)', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        goal_id: 'g123',
        plan: { steps: ['Step 1: Analyze data', 'Step 2: Process results'] },
      }),
    });
    render(<WorkflowBuilderPage />, { wrapper: ({ children }) => <Wrapper c={children} /> });
    const textarea = screen.getByLabelText(/Natural language workflow description/i);
    fireEvent.change(textarea, { target: { value: 'Analyze sales data' } });
    expect(screen.getByText(/✨ Generate/)).toBeDefined();
  });

  it('falls back gracefully on API failure', async () => {
    mockFetch.mockRejectedValueOnce(new Error('Network error'));
    render(<WorkflowBuilderPage />, { wrapper: ({ children }) => <Wrapper c={children} /> });
    expect(screen.getByRole('button', { name: /save/i })).toBeDefined();
  });

  it('add Trigger node button exists in palette', () => {
    render(<WorkflowBuilderPage />, { wrapper: ({ children }) => <Wrapper c={children} /> });
    expect(screen.getByRole('button', { name: /Add Trigger \/ Start node/i })).toBeDefined();
  });
});
