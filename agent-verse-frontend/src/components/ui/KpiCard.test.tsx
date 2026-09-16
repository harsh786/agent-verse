import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';
import React, { type ReactNode } from 'react';
import { Activity } from 'lucide-react';
import { KpiCard } from './KpiCard';

vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const makeStub = (tag: string) =>
    ({ children, ...props }: { children?: ReactNode; [k: string]: unknown }) =>
      React.createElement(tag, props as Record<string, unknown>, children);
  return {
    ...actual,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : makeStub(key)),
    }),
  };
});

describe('KpiCard', () => {
  test('renders label, value and sub text', () => {
    render(<KpiCard icon={Activity} label="Active Goals" value={1234} sub="last 24h" />);
    expect(screen.getByText('Active Goals')).toBeInTheDocument();
    expect(screen.getByText('1234')).toBeInTheDocument();
    expect(screen.getByText('last 24h')).toBeInTheDocument();
  });

  test('fires onClick when the card is pressed', async () => {
    const onClick = vi.fn();
    render(<KpiCard icon={Activity} label="Cost" value="$5" onClick={onClick} />);
    await userEvent.click(screen.getByRole('button'));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  test('hides the value and shows a skeleton while loading', () => {
    render(<KpiCard icon={Activity} label="Latency" value="99ms" isLoading />);
    expect(screen.queryByText('99ms')).not.toBeInTheDocument();
  });

  test('renders trend hints for up and neutral', () => {
    const { rerender } = render(<KpiCard icon={Activity} label="Throughput" value={5} trend="up" />);
    expect(screen.getByText(/trending up/i)).toBeInTheDocument();

    rerender(<KpiCard icon={Activity} label="Throughput" value={5} trend="neutral" />);
    expect(screen.getByText(/stable/i)).toBeInTheDocument();
  });

  test('hides trend hints while loading', () => {
    render(<KpiCard icon={Activity} label="Throughput" value={5} trend="down" isLoading />);
    expect(screen.queryByText(/trending down/i)).not.toBeInTheDocument();
  });
});
