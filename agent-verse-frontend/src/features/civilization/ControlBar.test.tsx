import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ControlBar } from './ControlBar';

function makeProps(overrides: Partial<React.ComponentProps<typeof ControlBar>> = {}) {
  return {
    civilizationId: 'civ-1',
    status: 'active',
    onPause: vi.fn().mockResolvedValue(undefined),
    onResume: vi.fn().mockResolvedValue(undefined),
    onSubmitGoal: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

describe('ControlBar', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the goal input and Run button', () => {
    render(<ControlBar {...makeProps()} />);
    expect(screen.getByLabelText('Goal input')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /run goal/i })).toBeInTheDocument();
  });

  it('submits a goal when the Run button is clicked and clears the input', async () => {
    const onSubmitGoal = vi.fn().mockResolvedValue(undefined);
    render(<ControlBar {...makeProps({ onSubmitGoal })} />);

    const input = screen.getByLabelText('Goal input') as HTMLInputElement;
    await userEvent.type(input, '  Explore the map  ');
    await userEvent.click(screen.getByRole('button', { name: /run goal/i }));

    expect(onSubmitGoal).toHaveBeenCalledWith('Explore the map');
    expect(input.value).toBe('');
  });

  it('submits a goal when Enter is pressed (without shift)', async () => {
    const onSubmitGoal = vi.fn().mockResolvedValue(undefined);
    render(<ControlBar {...makeProps({ onSubmitGoal })} />);

    const input = screen.getByLabelText('Goal input');
    await userEvent.type(input, 'Build a road{Enter}');

    expect(onSubmitGoal).toHaveBeenCalledWith('Build a road');
  });

  it('does not submit on shift+Enter', async () => {
    const onSubmitGoal = vi.fn().mockResolvedValue(undefined);
    render(<ControlBar {...makeProps({ onSubmitGoal })} />);

    const input = screen.getByLabelText('Goal input');
    await userEvent.type(input, 'Multi-line goal');
    await userEvent.type(input, '{Shift>}{Enter}{/Shift}');

    expect(onSubmitGoal).not.toHaveBeenCalled();
  });

  it('does not submit an empty or whitespace-only goal', async () => {
    const onSubmitGoal = vi.fn().mockResolvedValue(undefined);
    render(<ControlBar {...makeProps({ onSubmitGoal })} />);

    const input = screen.getByLabelText('Goal input');
    await userEvent.type(input, '   ');
    await userEvent.click(screen.getByRole('button', { name: /run goal/i }));

    expect(onSubmitGoal).not.toHaveBeenCalled();
  });

  it('disables the input and Run button, and shows a paused placeholder, when paused', () => {
    render(<ControlBar {...makeProps({ status: 'paused' })} />);
    const input = screen.getByLabelText('Goal input') as HTMLInputElement;
    expect(input).toBeDisabled();
    expect(input.placeholder).toMatch(/civilization paused/i);
    expect(screen.getByRole('button', { name: /run goal/i })).toBeDisabled();
  });

  it('does not submit a goal while paused even via Enter', async () => {
    const onSubmitGoal = vi.fn().mockResolvedValue(undefined);
    render(<ControlBar {...makeProps({ status: 'paused', onSubmitGoal })} />);
    const input = screen.getByLabelText('Goal input');
    // Input is disabled, so typing is a no-op, but exercise the handler path anyway.
    await userEvent.type(input, '{Enter}');
    expect(onSubmitGoal).not.toHaveBeenCalled();
  });

  it('calls onResume when paused and the toggle button is clicked', async () => {
    const onResume = vi.fn().mockResolvedValue(undefined);
    const onPause = vi.fn().mockResolvedValue(undefined);
    render(<ControlBar {...makeProps({ status: 'paused', onResume, onPause })} />);

    await userEvent.click(screen.getByRole('button', { name: /resume civilization/i }));
    expect(onResume).toHaveBeenCalledTimes(1);
    expect(onPause).not.toHaveBeenCalled();
  });

  it('calls onPause when active and the toggle button is clicked', async () => {
    const onResume = vi.fn().mockResolvedValue(undefined);
    const onPause = vi.fn().mockResolvedValue(undefined);
    render(<ControlBar {...makeProps({ status: 'active', onResume, onPause })} />);

    await userEvent.click(screen.getByRole('button', { name: /pause civilization/i }));
    expect(onPause).toHaveBeenCalledTimes(1);
    expect(onResume).not.toHaveBeenCalled();
  });

  it('shows the status pill text matching the status prop', () => {
    render(<ControlBar {...makeProps({ status: 'idle' })} />);
    expect(screen.getByText('idle')).toBeInTheDocument();
  });

  it('does not render the budget control when onAdjustBudget is not provided', () => {
    render(<ControlBar {...makeProps()} />);
    expect(screen.queryByLabelText('Adjust budget')).not.toBeInTheDocument();
  });

  it('renders current budget and opens/closes the budget popover', async () => {
    const onAdjustBudget = vi.fn().mockResolvedValue(undefined);
    render(<ControlBar {...makeProps({ onAdjustBudget, currentBudget: 500 })} />);

    expect(screen.getByText('$500')).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText('Adjust budget'));
    expect(screen.getByText('Adjust Total Budget')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('USD')).toBeInTheDocument();
  });

  it('saves a valid budget value and closes the popover', async () => {
    const onAdjustBudget = vi.fn().mockResolvedValue(undefined);
    render(<ControlBar {...makeProps({ onAdjustBudget, currentBudget: 100 })} />);

    await userEvent.click(screen.getByLabelText('Adjust budget'));
    const budgetInput = screen.getByPlaceholderText('USD');
    await userEvent.clear(budgetInput);
    await userEvent.type(budgetInput, '250');

    // Save via the checkmark button (only button inside the popover besides input)
    const popover = screen.getByText('Adjust Total Budget').closest('div')!;
    const saveButton = popover.querySelector('button')!;
    await userEvent.click(saveButton);

    expect(onAdjustBudget).toHaveBeenCalledWith(250);
  });

  it('saves a valid budget value on Enter key inside the budget input', async () => {
    const onAdjustBudget = vi.fn().mockResolvedValue(undefined);
    render(<ControlBar {...makeProps({ onAdjustBudget, currentBudget: 100 })} />);

    await userEvent.click(screen.getByLabelText('Adjust budget'));
    const budgetInput = screen.getByPlaceholderText('USD');
    await userEvent.clear(budgetInput);
    await userEvent.type(budgetInput, '75{Enter}');

    expect(onAdjustBudget).toHaveBeenCalledWith(75);
  });

  it('does not call onAdjustBudget for an invalid (zero/negative/NaN) budget value', async () => {
    const onAdjustBudget = vi.fn().mockResolvedValue(undefined);
    render(<ControlBar {...makeProps({ onAdjustBudget, currentBudget: 100 })} />);

    await userEvent.click(screen.getByLabelText('Adjust budget'));
    const budgetInput = screen.getByPlaceholderText('USD');
    await userEvent.clear(budgetInput);
    await userEvent.type(budgetInput, '-5{Enter}');

    expect(onAdjustBudget).not.toHaveBeenCalled();
  });

  it('renders without a currentBudget value (dash fallback in title)', () => {
    const onAdjustBudget = vi.fn().mockResolvedValue(undefined);
    render(<ControlBar {...makeProps({ onAdjustBudget, currentBudget: undefined })} />);
    expect(screen.getByLabelText('Adjust budget')).toHaveAttribute('title', 'Budget: $—');
  });
});
