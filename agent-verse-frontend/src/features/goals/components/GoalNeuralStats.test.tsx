import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';

// Real motion works in jsdom; nothing async here, so no framer mock needed.
import { GoalNeuralStats } from './GoalNeuralStats';

const ZERO = {
  tokenInput: 0,
  tokenOutput: 0,
  costUsd: 0,
  guardrailFired: 0,
  guardrailBlocked: 0,
  hitlPending: 0,
  hitlApproved: 0,
};

afterEach(() => vi.restoreAllMocks());

describe('GoalNeuralStats', () => {
  test('renders nothing when every counter is zero', () => {
    const { container } = render(<GoalNeuralStats {...ZERO} />);
    expect(container.firstChild).toBeNull();
  });

  test('shows the summed token count and truncated model name', () => {
    render(<GoalNeuralStats {...ZERO} tokenInput={1200} tokenOutput={800} model="claude-opus-4-super-long-name" />);
    expect(screen.getByText(/2,000 tok/)).toBeInTheDocument();
    // model is sliced to 16 chars.
    expect(screen.getByText('claude-opus-4-su')).toBeInTheDocument();
  });

  test('omits the cost pill when cost is zero and shows it when positive', () => {
    const { rerender } = render(<GoalNeuralStats {...ZERO} tokenInput={10} />);
    expect(screen.queryByText(/^\$/)).not.toBeInTheDocument();
    rerender(<GoalNeuralStats {...ZERO} tokenInput={10} costUsd={0.1234} />);
    expect(screen.getByText('$0.1234')).toBeInTheDocument();
  });

  test('renders the guardrail-blocked pill when guardrails fired', () => {
    render(<GoalNeuralStats {...ZERO} guardrailFired={3} />);
    expect(screen.getByText('3 blocked')).toBeInTheDocument();
  });

  test('pluralises the HITL pending approvals label', () => {
    const { rerender } = render(<GoalNeuralStats {...ZERO} hitlPending={1} />);
    expect(screen.getByText('1 approval')).toBeInTheDocument();
    rerender(<GoalNeuralStats {...ZERO} hitlPending={2} />);
    expect(screen.getByText('2 approvals')).toBeInTheDocument();
  });

  test('renders the HITL approved pill', () => {
    render(<GoalNeuralStats {...ZERO} hitlApproved={4} />);
    expect(screen.getByText('4 approved')).toBeInTheDocument();
  });

  test('renders (not null) when only HITL pending is set even with zero tokens', () => {
    render(<GoalNeuralStats {...ZERO} hitlPending={1} />);
    expect(screen.getByLabelText('Goal execution statistics')).toBeInTheDocument();
    // Token pill is suppressed when totalTokens is 0.
    expect(screen.queryByText(/tok/)).not.toBeInTheDocument();
  });
});
