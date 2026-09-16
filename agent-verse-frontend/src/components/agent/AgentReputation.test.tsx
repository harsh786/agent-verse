import { render, screen } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';
import React, { type ReactNode } from 'react';
import { AgentReputation } from './AgentReputation';

// Stub framer-motion so animated rings/bars render at their resting state.
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const makeStub = (tag: string) =>
    ({ children, ...props }: { children?: ReactNode; [k: string]: unknown }) =>
      React.createElement(tag, props as Record<string, unknown>, children);
  return {
    ...actual,
    useReducedMotion: () => true,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : makeStub(key)),
    }),
  };
});

describe('AgentReputation', () => {
  test('derives the tier label from the score when no tier is given', () => {
    const { rerender } = render(<AgentReputation score={95} />);
    expect(screen.getByText('Legendary')).toBeInTheDocument();

    rerender(<AgentReputation score={60} />);
    expect(screen.getByText('Proficient')).toBeInTheDocument();

    rerender(<AgentReputation score={10} />);
    expect(screen.getByText('New')).toBeInTheDocument();
  });

  test('an explicit tier prop overrides the score-derived tier', () => {
    render(<AgentReputation score={95} tier="learning" />);
    expect(screen.getByText('Learning')).toBeInTheDocument();
    expect(screen.queryByText('Legendary')).not.toBeInTheDocument();
  });

  test('renders completed goals and success rate as a percentage', () => {
    render(<AgentReputation score={80} completedGoals={42} successRate={0.75} />);
    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.getByText(/goals completed/i)).toBeInTheDocument();
    expect(screen.getByText('75%')).toBeInTheDocument();
    expect(screen.getByText(/success rate/i)).toBeInTheDocument();
  });

  test('shows the absolute trend value and exposes score on the progressbar', () => {
    render(<AgentReputation score={70} trend={-5} />);
    // Math.abs(-5) → 5%
    expect(screen.getByText('5%')).toBeInTheDocument();
    const bar = screen.getByRole('progressbar');
    expect(bar).toHaveAttribute('aria-valuenow', '70');
  });

  test('compact mode renders the score and an accessible reputation label', () => {
    render(<AgentReputation score={88} compact />);
    expect(screen.getByText('88')).toBeInTheDocument();
    expect(screen.getByText('Expert')).toBeInTheDocument();
    expect(screen.getByLabelText(/Reputation: 88\/100 — Expert/i)).toBeInTheDocument();
  });
});
