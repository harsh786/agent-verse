import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { Slider } from './slider';

describe('Slider', () => {
  it('renders a range input with the given value, min, max and step', () => {
    render(<Slider value={[42]} min={0} max={100} step={5} />);
    const input = screen.getByRole('slider') as HTMLInputElement;
    expect(input).toHaveAttribute('type', 'range');
    expect(input.value).toBe('42');
    expect(input).toHaveAttribute('min', '0');
    expect(input).toHaveAttribute('max', '100');
    expect(input).toHaveAttribute('step', '5');
  });

  it('defaults to min/0/100/1 when value and range props are omitted', () => {
    render(<Slider />);
    const input = screen.getByRole('slider') as HTMLInputElement;
    expect(input.value).toBe('0');
    expect(input).toHaveAttribute('min', '0');
    expect(input).toHaveAttribute('max', '100');
    expect(input).toHaveAttribute('step', '1');
  });

  it('calls onValueChange with the new value wrapped in an array when changed', async () => {
    const onValueChange = vi.fn();
    render(<Slider value={[10]} onValueChange={onValueChange} />);
    const input = screen.getByRole('slider');
    // fireEvent-style change via user-event's fill for range inputs isn't
    // supported, so dispatch the change event directly through fireEvent.
    const { fireEvent } = await import('@testing-library/react');
    fireEvent.change(input, { target: { value: '75' } });
    expect(onValueChange).toHaveBeenCalledWith([75]);
  });

  it('applies the disabled attribute and custom className', () => {
    render(<Slider value={[1]} disabled className="my-slider" />);
    const input = screen.getByRole('slider');
    expect(input).toBeDisabled();
    expect(input).toHaveClass('my-slider');
  });

  it('does not throw when changed with no onValueChange handler provided', () => {
    render(<Slider value={[1]} />);
    const input = screen.getByRole('slider');
    expect(() => input.dispatchEvent(new Event('change', { bubbles: true }))).not.toThrow();
  });
});
