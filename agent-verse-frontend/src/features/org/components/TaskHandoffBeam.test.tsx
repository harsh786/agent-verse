import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import React, { type ReactNode } from 'react';
import { TaskHandoffLine, TaskHandoffLabel } from './TaskHandoffBeam';

// Stub framer-motion so the animated packet renders at its resting state,
// matching the convention used elsewhere in this codebase (see AgentReputation.test.tsx).
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

describe('TaskHandoffLine', () => {
  it('renders an SVG line between the from/to points with the default duration', () => {
    const { container } = render(
      <svg>
        <TaskHandoffLine from={{ x: 1, y: 2 }} to={{ x: 10, y: 20 }} />
      </svg>,
    );
    const line = container.querySelector('line');
    expect(line).toBeTruthy();
    expect(line).toHaveAttribute('x1', '1');
    expect(line).toHaveAttribute('y1', '2');
    expect(line).toHaveAttribute('x2', '10');
    expect(line).toHaveAttribute('y2', '20');
    const animate = container.querySelector('animate');
    expect(animate).toHaveAttribute('dur', '1.40s');
  });

  it('honors a custom durationMs for the dash animation', () => {
    const { container } = render(
      <svg>
        <TaskHandoffLine from={{ x: 0, y: 0 }} to={{ x: 5, y: 5 }} durationMs={2000} />
      </svg>,
    );
    expect(container.querySelector('animate')).toHaveAttribute('dur', '2.00s');
  });
});

describe('TaskHandoffLabel', () => {
  it('renders the label text when not in reduced-motion mode', () => {
    render(<TaskHandoffLabel from={{ x: 0, y: 0 }} to={{ x: 10, y: 10 }} label="Deploy step" />);
    expect(screen.getByText('Deploy step')).toBeInTheDocument();
  });

  it('renders nothing when reduce is true', () => {
    const { container } = render(
      <TaskHandoffLabel from={{ x: 0, y: 0 }} to={{ x: 10, y: 10 }} label="Deploy step" reduce />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
