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
});
