/**
 * ThemedBarChart — tests both layout branches (horizontal/vertical), the
 * legend-only-when-multi-series branch, and the optional formatValue callback
 * wired into YAxis/XAxis tickFormatter and the Tooltip formatter.
 *
 * recharts needs layout APIs jsdom doesn't provide, so it's mocked to plain
 * divs; the mocks invoke render-prop callbacks (tickFormatter, formatter)
 * the same way real recharts would, so ThemedBarChart's own logic branches
 * get exercised.
 */
import type { ReactNode } from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';
import { ThemedBarChart } from './ThemedBarChart';

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children?: ReactNode }) => <div data-testid="rc">{children}</div>,
  BarChart: ({ children, layout }: { children?: ReactNode; layout?: string }) => (
    <div data-testid="bar-chart" data-layout={layout}>{children}</div>
  ),
  Bar: (props: { dataKey: string; name?: string; fill?: string }) => (
    <div data-testid="bar" data-key={props.dataKey} data-name={props.name} data-fill={props.fill} />
  ),
  XAxis: (props: { type?: string; tickFormatter?: (v: number) => unknown; dataKey?: string }) => {
    const out = props.tickFormatter?.(42);
    return <div data-testid="xaxis" data-type={props.type} data-key={props.dataKey} data-out={String(out)} />;
  },
  YAxis: (props: { type?: string; tickFormatter?: (v: number) => unknown; dataKey?: string }) => {
    const out = props.tickFormatter?.(7);
    return <div data-testid="yaxis" data-type={props.type} data-key={props.dataKey} data-out={String(out)} />;
  },
  CartesianGrid: () => <div data-testid="grid" />,
  Tooltip: (props: { formatter?: (v: number) => unknown }) => {
    const out = props.formatter ? props.formatter(3.14159) : undefined;
    return <div data-testid="tooltip" data-out={JSON.stringify(out)} />;
  },
  Legend: () => <div data-testid="legend" />,
}));

const DATA = [
  { name: 'Mon', a: 10, b: 20 },
  { name: 'Tue', a: 15, b: 5 },
];

describe('ThemedBarChart', () => {
  test('defaults to horizontal layout with a category XAxis and numeric YAxis', () => {
    render(<ThemedBarChart data={DATA} bars={[{ key: 'a' }]} xKey="name" />);
    expect(screen.getByTestId('bar-chart')).toHaveAttribute('data-layout', 'horizontal');
    expect(screen.getByTestId('xaxis')).toHaveAttribute('data-key', 'name');
    expect(screen.queryByTestId('yaxis')).not.toHaveAttribute('data-type', 'category');
  });

  test('vertical layout swaps axes: numeric XAxis, category YAxis on xKey', () => {
    render(<ThemedBarChart data={DATA} bars={[{ key: 'a' }]} xKey="name" layout="vertical" />);
    expect(screen.getByTestId('bar-chart')).toHaveAttribute('data-layout', 'vertical');
    expect(screen.getByTestId('xaxis')).toHaveAttribute('data-type', 'number');
    expect(screen.getByTestId('yaxis')).toHaveAttribute('data-type', 'category');
    expect(screen.getByTestId('yaxis')).toHaveAttribute('data-key', 'name');
  });

  test('legend is hidden for a single bar series', () => {
    render(<ThemedBarChart data={DATA} bars={[{ key: 'a' }]} xKey="name" />);
    expect(screen.queryByTestId('legend')).not.toBeInTheDocument();
  });

  test('legend renders for multiple bar series, and unlabeled bars fall back to their key', () => {
    render(<ThemedBarChart data={DATA} bars={[{ key: 'a' }, { key: 'b', label: 'Series B' }]} xKey="name" />);
    expect(screen.getByTestId('legend')).toBeInTheDocument();
    const bars = screen.getAllByTestId('bar');
    expect(bars).toHaveLength(2);
    expect(bars[0]).toHaveAttribute('data-name', 'a');
    expect(bars[1]).toHaveAttribute('data-name', 'Series B');
  });

  test('bars without an explicit color cycle through the theme palette by index', () => {
    render(<ThemedBarChart data={DATA} bars={[{ key: 'a' }, { key: 'b', color: '#custom' }]} xKey="name" />);
    const bars = screen.getAllByTestId('bar');
    expect(bars[0].getAttribute('data-fill')).toMatch(/^hsl\(/);
    expect(bars[1]).toHaveAttribute('data-fill', '#custom');
  });

  test('without formatValue, XAxis/YAxis ticks and Tooltip formatter are left undefined', () => {
    render(<ThemedBarChart data={DATA} bars={[{ key: 'a' }]} xKey="name" />);
    expect(screen.getByTestId('yaxis')).toHaveAttribute('data-out', 'undefined');
    expect(screen.getByTestId('tooltip')).not.toHaveAttribute('data-out');
  });

  test('formatValue formats YAxis ticks and the Tooltip value', () => {
    const formatValue = (v: number) => `$${v}`;
    render(<ThemedBarChart data={DATA} bars={[{ key: 'a' }]} xKey="name" formatValue={formatValue} />);
    expect(screen.getByTestId('yaxis')).toHaveAttribute('data-out', '$7');
    expect(screen.getByTestId('tooltip')).toHaveAttribute('data-out', JSON.stringify(['$3.14159']));
  });

  test('applies a custom className to the wrapper', () => {
    const { container } = render(
      <ThemedBarChart data={DATA} bars={[{ key: 'a' }]} xKey="name" className="my-chart" />,
    );
    expect(container.querySelector('.my-chart')).toBeInTheDocument();
  });
});
