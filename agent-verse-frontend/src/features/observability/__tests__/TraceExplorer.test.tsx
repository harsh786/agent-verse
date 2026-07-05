import { describe, it } from 'vitest';
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { TraceExplorer } from '../TraceExplorer';

const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
describe('TraceExplorer', () => {
  it('renders without crashing', () => {
    render(<QueryClientProvider client={qc}><TraceExplorer /></QueryClientProvider>);
  });
});
