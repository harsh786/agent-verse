/**
 * ThemedRadarChart — tests the compareData merge/overlay branch, the legend
 * shown only when comparing, the Tooltip formatter's name-based label switch,
 * and default vs custom color/label props.
 */
import type { ReactNode } from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';
import { ThemedRadarChart } from './ThemedRadarChart';

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  RadarChart: ({ children, data }: { children?: ReactNode; data?: unknown[] }) => (
    <div data-testid="radar-chart" data-rows={JSON.stringify(data)}>{children}</div>
  ),
  Radar: (props: { dataKey: string; name?: string; stroke?: string; strokeDasharray?: string }) => (
    <div
      data-testid="radar"
      data-key={props.dataKey}
      data-name={props.name}
      data-stroke={props.stroke}
      data-dash={props.strokeDasharray ?? ''}
    />
  ),
  PolarGrid: () => <div data-testid="polar-grid" />,
  PolarAngleAxis: (props: { dataKey?: string }) => <div data-testid="angle-axis" data-key={props.dataKey} />,
  PolarRadiusAxis: () => <div data-testid="radius-axis" />,
  Tooltip: (props: { formatter?: (v: number, name: string) => unknown }) => {
    const valueOut = props.formatter?.(0.5, 'value');
    const compareOut = props.formatter?.(0.25, 'compareValue');
    return (
      <div
        data-testid="tooltip"
        data-value-out={JSON.stringify(valueOut)}
        data-compare-out={JSON.stringify(compareOut)}
      />
    );
  },
  Legend: () => <div data-testid="legend" />,
}));

const DATA = [
  { metric: 'speed', value: 0.8 },
  { metric: 'accuracy', value: 0.6 },
];

const COMPARE = [
  { metric: 'speed', value: 0.4 },
  // 'accuracy' intentionally missing to exercise the ?? 0 fallback
];

describe('ThemedRadarChart', () => {
  test('renders a single Radar series with default label/color when no compareData', () => {
    render(<ThemedRadarChart data={DATA} />);
    expect(screen.getByTestId('radar')).toHaveAttribute('data-key', 'value');
    expect(screen.getByTestId('radar')).toHaveAttribute('data-name', 'Score');
    expect(screen.getByTestId('radar').getAttribute('data-stroke')).toMatch(/^hsl\(/);
    expect(screen.queryByTestId('legend')).not.toBeInTheDocument();
    // Only one Radar rendered (no compare overlay)
    expect(screen.getAllByTestId('radar')).toHaveLength(1);
  });

  test('merges compareData into chartData, filling missing metrics with 0', () => {
    render(<ThemedRadarChart data={DATA} compareData={COMPARE} />);
    const rows = JSON.parse(screen.getByTestId('radar-chart').getAttribute('data-rows') ?? '[]');
    expect(rows).toEqual([
      { metric: 'speed', value: 0.8, compareValue: 0.4 },
      { metric: 'accuracy', value: 0.6, compareValue: 0 },
    ]);
  });

  test('renders a second dashed Radar and the legend when compareData is provided', () => {
    render(<ThemedRadarChart data={DATA} compareData={COMPARE} label="Mine" compareLabel="Theirs" />);
    expect(screen.getByTestId('legend')).toBeInTheDocument();
    const radars = screen.getAllByTestId('radar');
    expect(radars).toHaveLength(2);
    expect(radars[0]).toHaveAttribute('data-name', 'Mine');
    expect(radars[1]).toHaveAttribute('data-name', 'Theirs');
    expect(radars[1]).toHaveAttribute('data-dash', '4 2');
    expect(radars[0]).toHaveAttribute('data-dash', '');
  });

  test('custom color/compareColor override the theme palette defaults', () => {
    render(<ThemedRadarChart data={DATA} compareData={COMPARE} color="#111" compareColor="#222" />);
    const radars = screen.getAllByTestId('radar');
    expect(radars[0]).toHaveAttribute('data-stroke', '#111');
    expect(radars[1]).toHaveAttribute('data-stroke', '#222');
  });

  test('tooltip formatter labels the primary series by "label" and any other by "compareLabel"', () => {
    render(<ThemedRadarChart data={DATA} label="Mine" compareLabel="Theirs" />);
    const tip = screen.getByTestId('tooltip');
    expect(tip).toHaveAttribute('data-value-out', JSON.stringify(['0.500', 'Mine']));
    expect(tip).toHaveAttribute('data-compare-out', JSON.stringify(['0.250', 'Theirs']));
  });

  test('applies a custom className to the wrapper', () => {
    const { container } = render(<ThemedRadarChart data={DATA} className="my-radar" />);
    expect(container.querySelector('.my-radar')).toBeInTheDocument();
  });
});
