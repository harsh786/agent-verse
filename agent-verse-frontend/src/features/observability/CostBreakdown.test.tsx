import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { CostBreakdown } from './CostBreakdown';

const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

describe('CostBreakdown', () => {
  it('renders without crashing for a valid goalId', () => {
    render(
      <QueryClientProvider client={qc}>
        <CostBreakdown goalId="test-goal-123" />
      </QueryClientProvider>
    );
    // Loading state or empty — should not throw
    expect(document.body).toBeTruthy();
  });
});
