import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import BuilderPage from '../BuilderPage';

const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
describe('BuilderPage', () => {
  it('renders without crashing', () => {
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <BuilderPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByText('AI Project Builder')).toBeTruthy();
  });
});
