import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { ConditionFamilyForm } from './ConditionFamilyForm';

/** Grab the object the component most recently handed to onChange. */
function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

afterEach(() => vi.restoreAllMocks());

describe('ConditionFamilyForm', () => {
  test('renders the CEL expression field (with hint) for the condition type', () => {
    render(<ConditionFamilyForm triggerType="condition" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('CEL Condition Expression')).toBeInTheDocument();
    // The blue helper box explains payload access + timeout.
    expect(screen.getByText(/Expressions timeout after 500ms/i)).toBeInTheDocument();
  });

  test('shows the passed condition_expression value', () => {
    render(
      <ConditionFamilyForm
        triggerType="condition"
        value={{ condition_expression: 'payload.amount > 42' }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByDisplayValue('payload.amount > 42')).toBeInTheDocument();
  });

  test('editing the CEL expression calls onChange with the merged spec', () => {
    const onChange = vi.fn();
    render(<ConditionFamilyForm triggerType="condition" value={{ description: 'x' }} onChange={onChange} />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'payload.ok == true' } });
    expect(onChange).toHaveBeenCalled();
    expect(lastArg(onChange)).toEqual({ description: 'x', condition_expression: 'payload.ok == true' });
  });

  test('counter_threshold renders its key + numeric threshold and coerces to a number', () => {
    const onChange = vi.fn();
    render(<ConditionFamilyForm triggerType="counter_threshold" value={{}} onChange={onChange} />);
    expect(screen.getByPlaceholderText('api_calls_per_user')).toBeInTheDocument();
    // Threshold defaults to 100 — change it and assert a numeric (not string) payload.
    fireEvent.change(screen.getByDisplayValue('100'), { target: { value: '250' } });
    expect(lastArg(onChange)).toEqual({ counter_threshold: 250 });
  });

  test('compound type splits comma-separated child trigger ids into an array', () => {
    const onChange = vi.fn();
    render(<ConditionFamilyForm triggerType="compound" value={{}} onChange={onChange} />);
    expect(screen.getByText('Combine Logic')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('trig-abc, trig-def'), {
      target: { value: 'trig-a, trig-b ,  ' },
    });
    expect(lastArg(onChange)).toEqual({ compound_trigger_ids: ['trig-a', 'trig-b'] });
  });

  test('window_aggregate select updates the aggregation function', () => {
    const onChange = vi.fn();
    render(<ConditionFamilyForm triggerType="window_aggregate" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue('sum'), { target: { value: 'avg' } });
    expect(lastArg(onChange)).toEqual({ window_aggregation: 'avg' });
  });
});
