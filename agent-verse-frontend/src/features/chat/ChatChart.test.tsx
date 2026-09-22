import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { ChatChart } from './ChatChart';

describe('ChatChart', () => {
  test('shows the empty state when data is omitted', () => {
    render(<ChatChart />);
    expect(screen.getByText('No chart data')).toBeInTheDocument();
  });

  test('shows the empty state when data is an empty array', () => {
    render(<ChatChart data={[]} title="Empty" />);
    expect(screen.getByText('No chart data')).toBeInTheDocument();
  });

  test('renders a bar per data point sized relative to the max value', () => {
    render(<ChatChart data={[{ label: 'A', value: 10 }, { label: 'B', value: 20 }]} />);
    expect(screen.getByText('A')).toBeInTheDocument();
    expect(screen.getByText('B')).toBeInTheDocument();
    const bars = screen.getAllByRole('presentation');
    expect(bars).toHaveLength(2);
    expect(bars[0]).toHaveStyle({ width: '50%' });
    expect(bars[1]).toHaveStyle({ width: '100%' });
  });

  test('renders the title when provided, and uses it as the accessible label', () => {
    render(<ChatChart data={[{ label: 'A', value: 1 }]} title="Revenue" />);
    expect(screen.getByText('Revenue')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Revenue' })).toBeInTheDocument();
  });

  test('omits the title paragraph and falls back to a default aria-label when no title is given', () => {
    render(<ChatChart data={[{ label: 'A', value: 1 }]} />);
    expect(screen.getByRole('img', { name: 'Chart' })).toBeInTheDocument();
  });

  test('guards against an all-zero dataset (max floors at 1, no division by zero)', () => {
    render(<ChatChart data={[{ label: 'A', value: 0 }, { label: 'B', value: 0 }]} />);
    const bars = screen.getAllByRole('presentation');
    expect(bars[0]).toHaveStyle({ width: '0%' });
  });
});
