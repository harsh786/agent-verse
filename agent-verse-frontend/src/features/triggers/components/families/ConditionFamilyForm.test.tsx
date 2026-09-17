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

  test('counter_threshold key and window fields report edits', () => {
    const onChange = vi.fn();
    render(<ConditionFamilyForm triggerType="counter_threshold" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('api_calls_per_user'), { target: { value: 'logins' } });
    expect(lastArg(onChange)).toEqual({ counter_key: 'logins' });
    fireEvent.change(screen.getByDisplayValue('3600'), { target: { value: '600' } });
    expect(lastArg(onChange)).toEqual({ counter_window_secs: 600 });
  });

  test('state_transition renders machine id, from/to state and reports edits', () => {
    const onChange = vi.fn();
    render(<ConditionFamilyForm triggerType="state_transition" value={{}} onChange={onChange} />);
    expect(screen.getByText('State Machine ID')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('machine-uuid'), { target: { value: 'wf-1' } });
    expect(lastArg(onChange)).toEqual({ state_machine_id: 'wf-1' });
    fireEvent.change(screen.getByPlaceholderText('pending'), { target: { value: 'processing' } });
    expect(lastArg(onChange)).toEqual({ from_state: 'processing' });
    fireEvent.change(screen.getByPlaceholderText('completed'), { target: { value: 'done' } });
    expect(lastArg(onChange)).toEqual({ to_state: 'done' });
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

  test('window_aggregate field, window seconds, and threshold report edits', () => {
    const onChange = vi.fn();
    render(<ConditionFamilyForm triggerType="window_aggregate" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('payload.amount'), { target: { value: 'payload.total' } });
    expect(lastArg(onChange)).toEqual({ window_field: 'payload.total' });
    fireEvent.change(screen.getByDisplayValue('3600'), { target: { value: '60' } });
    expect(lastArg(onChange)).toEqual({ window_seconds: 60 });
    fireEvent.change(screen.getByDisplayValue('100'), { target: { value: '50' } });
    expect(lastArg(onChange)).toEqual({ window_threshold: 50 });
  });

  test('compound logic select reports edits', () => {
    const onChange = vi.fn();
    render(<ConditionFamilyForm triggerType="compound" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue('AND (all must fire)'), { target: { value: 'OR' } });
    expect(lastArg(onChange)).toEqual({ compound_logic: 'OR' });
  });

  test('compound preserves an existing compound_trigger_ids array in the joined display', () => {
    render(
      <ConditionFamilyForm
        triggerType="compound"
        value={{ compound_trigger_ids: ['trig-1', 'trig-2'] }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByDisplayValue('trig-1, trig-2')).toBeInTheDocument();
  });

  test('renders nothing for an unrelated trigger type', () => {
    const { container } = render(
      <ConditionFamilyForm triggerType="cron" value={{}} onChange={vi.fn()} />,
    );
    expect(container.querySelector('.space-y-4')?.children.length).toBe(0);
  });
});
