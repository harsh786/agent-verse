/**
 * ThemedLineChart — tests the legend-only-when-multi-series branch and the
 * optional formatValue callback wired into YAxis tickFormatter / Tooltip.
 */
import type { ReactNode } from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';
import { ThemedLineChart } from './ThemedLineChart';

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  LineChart: ({ children }: { children?: ReactNode }) => <div data-testid="line-chart">{children}</div>,
  Line: (props: { dataKey: string; name?: string; stroke?: string }) => (
    <div data-testid="line" data-key={props.dataKey} data-name={props.name} data-stroke={props.stroke} />
  ),
  XAxis: (props: { dataKey?: string }) => <div data-testid="xaxis" data-key={props.dataKey} />,
  YAxis: (props: { tickFormatter?: (v: number) => unknown }) => {
    const out = props.tickFormatter?.(9);
    return <div data-testid="yaxis" data-out={String(out)} />;
  },
  CartesianGrid: () => <div data-testid="grid" />,
  Tooltip: (props: { formatter?: (v: number) => unknown }) => {
    const out = props.formatter ? props.formatter(2.71828) : undefined;
    return <div data-testid="tooltip" data-out={JSON.stringify(out)} />;
  },
  Legend: () => <div data-testid="legend" />,
}));

const DATA = [
  { t: '10:00', cpu: 40, mem: 60 },
  { t: '10:05', cpu: 55, mem: 65 },
];

describe('ThemedLineChart', () => {
  test('renders one Line per series and binds xKey to the XAxis', () => {
    render(<ThemedLineChart data={DATA} lines={[{ key: 'cpu' }]} xKey="t" />);
    expect(screen.getByTestId('xaxis')).toHaveAttribute('data-key', 't');
    expect(screen.getByTestId('line')).toHaveAttribute('data-key', 'cpu');
  });

  test('legend is hidden for a single line series', () => {
    render(<ThemedLineChart data={DATA} lines={[{ key: 'cpu' }]} xKey="t" />);
    expect(screen.queryByTestId('legend')).not.toBeInTheDocument();
  });

  test('legend renders for multiple line series, and unlabeled lines fall back to their key', () => {
    render(
      <ThemedLineChart
        data={DATA}
        lines={[{ key: 'cpu' }, { key: 'mem', label: 'Memory' }]}
        xKey="t"
      />,
    );
    expect(screen.getByTestId('legend')).toBeInTheDocument();
    const lines = screen.getAllByTestId('line');
    expect(lines[0]).toHaveAttribute('data-name', 'cpu');
    expect(lines[1]).toHaveAttribute('data-name', 'Memory');
  });

  test('lines without an explicit color cycle through the theme palette; explicit color wins', () => {
    render(
      <ThemedLineChart
        data={DATA}
        lines={[{ key: 'cpu' }, { key: 'mem', color: '#abcdef' }]}
        xKey="t"
      />,
    );
    const lines = screen.getAllByTestId('line');
    expect(lines[0].getAttribute('data-stroke')).toMatch(/^hsl\(/);
    expect(lines[1]).toHaveAttribute('data-stroke', '#abcdef');
  });

  test('without formatValue, YAxis ticks and Tooltip formatter stay undefined', () => {
    render(<ThemedLineChart data={DATA} lines={[{ key: 'cpu' }]} xKey="t" />);
    expect(screen.getByTestId('yaxis')).toHaveAttribute('data-out', 'undefined');
    expect(screen.getByTestId('tooltip')).not.toHaveAttribute('data-out');
  });

  test('formatValue formats YAxis ticks and the Tooltip value', () => {
    const formatValue = (v: number) => `${v.toFixed(1)}%`;
    render(<ThemedLineChart data={DATA} lines={[{ key: 'cpu' }]} xKey="t" formatValue={formatValue} />);
    expect(screen.getByTestId('yaxis')).toHaveAttribute('data-out', '9.0%');
    expect(screen.getByTestId('tooltip')).toHaveAttribute('data-out', JSON.stringify(['2.7%']));
  });

  test('applies a custom className to the wrapper', () => {
    const { container } = render(
      <ThemedLineChart data={DATA} lines={[{ key: 'cpu' }]} xKey="t" className="my-line-chart" />,
    );
    expect(container.querySelector('.my-line-chart')).toBeInTheDocument();
  });
});
