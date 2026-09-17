import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { HITLContextRenderer, HITLContextList, type ContextItem } from '../hitl/HITLContextRenderer';

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
}

describe('HITLContextRenderer', () => {
  it('renders json display type', () => {
    const item: ContextItem = { display_type: 'json', title: 'Payload', data: { key: 'value' } };
    wrap(<HITLContextRenderer item={item} />);
    expect(screen.getByText('Payload')).toBeInTheDocument();
    expect(screen.getByText(/"key"/)).toBeInTheDocument();
  });

  it('renders table display type with headers', () => {
    const item: ContextItem = {
      display_type: 'table',
      title: 'Results',
      data: [{ name: 'Alice', score: 95 }, { name: 'Bob', score: 87 }],
    };
    wrap(<HITLContextRenderer item={item} />);
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByText('name')).toBeInTheDocument();
  });

  it('renders diff display type with before/after', () => {
    const item: ContextItem = {
      display_type: 'diff',
      title: 'Config Change',
      data: { before: 'old value', after: 'new value' },
    };
    wrap(<HITLContextRenderer item={item} />);
    expect(screen.getByText('Before')).toBeInTheDocument();
    expect(screen.getByText('After')).toBeInTheDocument();
  });

  it('renders number display type with value', () => {
    const item: ContextItem = {
      display_type: 'number',
      title: 'Risk Score',
      data: { value: 0.85 },
      threshold_red: 0.9,
      threshold_yellow: 0.7,
    };
    wrap(<HITLContextRenderer item={item} />);
    expect(screen.getByText('Risk Score')).toBeInTheDocument();
  });

  it('renders list display type', () => {
    const item: ContextItem = {
      display_type: 'list',
      title: 'Issues',
      data: ['Missing signature', 'Expired document', 'Invalid address'],
    };
    wrap(<HITLContextRenderer item={item} />);
    expect(screen.getByRole('list')).toBeInTheDocument();
    expect(screen.getByText('Missing signature')).toBeInTheDocument();
  });

  it('renders image display type', () => {
    const item: ContextItem = {
      display_type: 'image',
      title: 'Document Scan',
      data: 'https://example.com/scan.jpg',
    };
    wrap(<HITLContextRenderer item={item} />);
    const img = screen.getByRole('img');
    expect(img).toHaveAttribute('src', 'https://example.com/scan.jpg');
  });

  it('renders chart display type fallback', () => {
    const item: ContextItem = {
      display_type: 'chart',
      title: 'Trend',
      data: [1, 2, 3],
    };
    wrap(<HITLContextRenderer item={item} />);
    expect(screen.getByText('Trend')).toBeInTheDocument();
  });

  it('HITLContextList renders multiple items', () => {
    const items: ContextItem[] = [
      { display_type: 'json', title: 'Item 1', data: {} },
      { display_type: 'list', title: 'Item 2', data: ['a', 'b'] },
    ];
    wrap(<HITLContextList items={items} />);
    expect(screen.getByText('Item 1')).toBeInTheDocument();
    expect(screen.getByText('Item 2')).toBeInTheDocument();
  });

  it('has accessible region for each item', () => {
    const item: ContextItem = { display_type: 'json', title: 'Data', data: {} };
    wrap(<HITLContextRenderer item={item} />);
    const region = screen.getByRole('region');
    expect(region).toHaveAttribute('aria-label');
  });

  it('renders json display type with string data as-is (not JSON.stringify)', () => {
    const item: ContextItem = { display_type: 'json', title: 'Raw', data: 'plain string payload' };
    wrap(<HITLContextRenderer item={item} />);
    expect(screen.getByText('plain string payload')).toBeInTheDocument();
  });

  it('renders table display type with empty data and no headers', () => {
    const item: ContextItem = { display_type: 'table', title: 'Empty Results', data: [] };
    wrap(<HITLContextRenderer item={item} />);
    const table = screen.getByRole('table');
    expect(table.querySelectorAll('th')).toHaveLength(0);
    expect(table.querySelectorAll('tbody tr')).toHaveLength(0);
  });

  it('renders table display type falling back to empty string for missing cell values', () => {
    const item: ContextItem = {
      display_type: 'table',
      title: 'Sparse',
      data: [{ name: 'Alice', score: null }],
    };
    wrap(<HITLContextRenderer item={item} />);
    expect(screen.getByText('Alice')).toBeInTheDocument();
  });

  it('renders diff display type falling back to empty strings when data is missing', () => {
    const item: ContextItem = { display_type: 'diff', title: 'No Data', data: null };
    const { container } = wrap(<HITLContextRenderer item={item} />);
    expect(screen.getByText('Before')).toBeInTheDocument();
    expect(screen.getByText('After')).toBeInTheDocument();
    const pres = container.querySelectorAll('pre');
    expect(pres[0]).toHaveTextContent('');
    expect(pres[1]).toHaveTextContent('');
  });

  it('renders number display type with a bare numeric value (not wrapped in object)', () => {
    const item: ContextItem = { display_type: 'number', title: 'Count', data: 42 };
    wrap(<HITLContextRenderer item={item} />);
    expect(screen.getByText('42')).toBeInTheDocument();
  });

  it('renders number display type defaulting to 0 when data is null', () => {
    const item: ContextItem = { display_type: 'number', title: 'Missing', data: null };
    wrap(<HITLContextRenderer item={item} />);
    expect(screen.getByText('0')).toBeInTheDocument();
  });

  it('renders number display type with a unit from the data object', () => {
    const item: ContextItem = { display_type: 'number', title: 'Latency', data: { value: 120, unit: 'ms' } };
    wrap(<HITLContextRenderer item={item} />);
    expect(screen.getByText('ms')).toBeInTheDocument();
  });

  it('renders only the yellow marker when just threshold_yellow is set', () => {
    const item: ContextItem = {
      display_type: 'number',
      title: 'Warm Score',
      data: { value: 0.75 },
      threshold_yellow: 0.7,
    };
    const { container } = wrap(<HITLContextRenderer item={item} />);
    expect(container.textContent).toContain('⚠️');
    expect(container.textContent).not.toContain('🔴');
  });

  it('renders only the red marker when just threshold_red is set', () => {
    const item: ContextItem = {
      display_type: 'number',
      title: 'Hot Score',
      data: { value: 0.95 },
      threshold_red: 0.9,
    };
    const { container } = wrap(<HITLContextRenderer item={item} />);
    expect(container.textContent).toContain('🔴');
    expect(container.textContent).not.toContain('⚠️');
  });

  it('renders number display type with no thresholds configured at all', () => {
    const item: ContextItem = { display_type: 'number', title: 'Plain', data: { value: 5 } };
    const { container } = wrap(<HITLContextRenderer item={item} />);
    expect(container.textContent).not.toContain('⚠️');
    expect(container.textContent).not.toContain('🔴');
  });

  it('renders list display type wrapping a non-array value into a single-item list', () => {
    const item: ContextItem = { display_type: 'list', title: 'Single', data: 'lonely item' };
    wrap(<HITLContextRenderer item={item} />);
    expect(screen.getByText('lonely item')).toBeInTheDocument();
  });

  it('renders list display type stringifying object entries', () => {
    const item: ContextItem = {
      display_type: 'list',
      title: 'Objects',
      data: [{ code: 'E1' }],
    };
    wrap(<HITLContextRenderer item={item} />);
    expect(screen.getByText('{"code":"E1"}')).toBeInTheDocument();
  });

  it('renders image display type coercing non-string data to a string src', () => {
    const item = { display_type: 'image', title: 'Bad Src', data: 12345 } as unknown as ContextItem;
    wrap(<HITLContextRenderer item={item} />);
    const img = screen.getByRole('img');
    expect(img).toHaveAttribute('src', '12345');
  });

  it('falls back to the JSON renderer for an unknown display_type', () => {
    const item = { display_type: 'unknown-type', title: 'Mystery', data: { a: 1 } } as unknown as ContextItem;
    wrap(<HITLContextRenderer item={item} />);
    expect(screen.getByText('Mystery')).toBeInTheDocument();
    expect(screen.getByText(/"a"/)).toBeInTheDocument();
  });

  it('HITLContextList renders nothing when given an empty items array', () => {
    const { container } = wrap(<HITLContextList items={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('HITLContextList renders nothing when items is undefined', () => {
    const { container } = wrap(<HITLContextList items={undefined as unknown as ContextItem[]} />);
    expect(container.firstChild).toBeNull();
  });
});
