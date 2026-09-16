import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect } from 'vitest';
import { ChatTokenCostBadge } from './ChatTokenCostBadge';

describe('ChatTokenCostBadge', () => {
  it('renders the total token count and an aria-label with the formatted cost', () => {
    render(<ChatTokenCostBadge tokensIn={10} tokensOut={5} costUsd={0.00234} model="claude-3" />);
    expect(screen.getByText('15')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '15 tokens, $0.00234' })).toBeInTheDocument();
  });

  it('defaults tokens and cost to 0 when no props are given', () => {
    render(<ChatTokenCostBadge />);
    expect(screen.getByText('0')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '0 tokens, $0.00000' })).toBeInTheDocument();
  });

  it('does not show the detail tooltip until hovered', () => {
    render(<ChatTokenCostBadge tokensIn={1} tokensOut={2} costUsd={0.1} model="gpt-4" />);
    expect(screen.queryByText('gpt-4')).not.toBeInTheDocument();
    expect(screen.queryByText('Input')).not.toBeInTheDocument();
  });

  it('reveals model, input, output and cost details on mouse enter, and hides them again on mouse leave', async () => {
    const user = userEvent.setup();
    render(<ChatTokenCostBadge tokensIn={100} tokensOut={50} costUsd={0.012} model="gpt-4" />);
    const button = screen.getByRole('button');

    await user.hover(button);
    expect(screen.getByText('gpt-4')).toBeInTheDocument();
    expect(screen.getByText('Input')).toBeInTheDocument();
    expect(screen.getByText('100 tokens')).toBeInTheDocument();
    expect(screen.getByText('Output')).toBeInTheDocument();
    expect(screen.getByText('50 tokens')).toBeInTheDocument();
    expect(screen.getByText('Cost')).toBeInTheDocument();
    expect(screen.getByText('$0.01200')).toBeInTheDocument();

    await user.unhover(button);
    expect(screen.queryByText('gpt-4')).not.toBeInTheDocument();
  });

  it('omits the model line in the tooltip when no model is given', async () => {
    const user = userEvent.setup();
    render(<ChatTokenCostBadge tokensIn={1} tokensOut={1} costUsd={0} />);
    await user.hover(screen.getByRole('button'));
    expect(screen.getByText('Input')).toBeInTheDocument();
    // No model name was passed, so no extra <p> with a model label appears.
    expect(screen.queryByText(/^claude|^gpt/i)).not.toBeInTheDocument();
  });
});
