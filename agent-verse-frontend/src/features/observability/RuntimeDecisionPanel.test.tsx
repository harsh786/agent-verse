import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test } from 'vitest';
import { RuntimeDecisionPanel } from './RuntimeDecisionPanel';

describe('RuntimeDecisionPanel', () => {
  test('renders the heading and tags the container with the goal id', () => {
    const { container } = render(<RuntimeDecisionPanel goalId="goal-1" />);
    expect(screen.getByText('Runtime Orchestration Decisions')).toBeInTheDocument();
    expect(container.querySelector('[data-goal-id="goal-1"]')).not.toBeNull();
  });

  test('shows the orchestration summary badges', () => {
    render(
      <RuntimeDecisionPanel
        goalId="g1"
        complexity="high"
        risk="critical"
        ragStrategy="hybrid"
        modelTier="premium"
        guardrailBundle="strict"
        assemblyLatencyMs={12.34}
      />,
    );
    expect(screen.getByText('high complexity')).toBeInTheDocument();
    expect(screen.getByText('critical risk')).toBeInTheDocument();
    expect(screen.getByText('RAG: hybrid')).toBeInTheDocument();
    expect(screen.getByText('model: premium')).toBeInTheDocument();
    expect(screen.getByText('guardrails: strict')).toBeInTheDocument();
    // toFixed(1)
    expect(screen.getByText(/profiled in 12\.3ms/)).toBeInTheDocument();
  });

  test('lists active patterns flattened across categories', () => {
    render(
      <RuntimeDecisionPanel
        goalId="g1"
        patternsActive={{ reasoning: ['chain_of_thought'], safety: ['guardrail_check'] }}
      />,
    );
    expect(screen.getByText('Active patterns:')).toBeInTheDocument();
    expect(screen.getByText('chain_of_thought')).toBeInTheDocument();
    expect(screen.getByText('guardrail_check')).toBeInTheDocument();
  });

  test('expands the decision trace to show each dimension and reason', async () => {
    render(
      <RuntimeDecisionPanel
        goalId="g1"
        decisions={[
          { selector: 's1', dimension: 'model', selected: 'gpt-x', reason: 'cheapest capable', latency_ms: 3 },
          { selector: 's2', dimension: 'rag', selected: 'hybrid', reason: 'high recall' },
        ]}
      />,
    );
    const summary = screen.getByText('Show 2 decisions');
    await userEvent.click(summary);
    expect(screen.getByText('model')).toBeInTheDocument();
    expect(screen.getByText('gpt-x')).toBeInTheDocument();
    expect(screen.getByText('(cheapest capable)')).toBeInTheDocument();
    expect(screen.getByText('rag')).toBeInTheDocument();
  });

  test('renders the eval score as a percentage, and hides it when null', () => {
    const { rerender } = render(<RuntimeDecisionPanel goalId="g1" overallScore={0.85} />);
    expect(screen.getByText('Eval score:')).toBeInTheDocument();
    expect(screen.getByText('85%')).toBeInTheDocument();

    rerender(<RuntimeDecisionPanel goalId="g1" overallScore={null} />);
    expect(screen.queryByText('Eval score:')).not.toBeInTheDocument();
  });

  test('a bare panel shows no decision trace or summary badges', () => {
    render(<RuntimeDecisionPanel goalId="g1" />);
    expect(screen.queryByText(/Show \d+ decisions/)).not.toBeInTheDocument();
    expect(screen.queryByText('Active patterns:')).not.toBeInTheDocument();
  });
});
