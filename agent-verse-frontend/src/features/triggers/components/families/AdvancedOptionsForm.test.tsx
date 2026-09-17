import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { AdvancedOptionsForm } from './AdvancedOptionsForm';

/** Grab the object the component most recently handed to onChange. */
function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

afterEach(() => vi.restoreAllMocks());

describe('AdvancedOptionsForm', () => {
  test('renders the collapsed summary and all fields with defaults', () => {
    render(<AdvancedOptionsForm value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Advanced options')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('e.g. Nightly invoice sync')).toHaveValue('');
    expect(screen.getByRole('combobox')).toHaveValue('normal');
    expect(screen.getByPlaceholderText('payload.amount > 1000')).toHaveValue('');
    expect(screen.getByPlaceholderText('#ops-alerts or ops@acme.com')).toHaveValue('');
    expect(screen.getByPlaceholderText('ops, billing')).toHaveValue('');
    expect(screen.getByRole('checkbox')).not.toBeChecked();
  });

  test('editing the label merges into value', () => {
    const onChange = vi.fn();
    render(<AdvancedOptionsForm value={{ priority: 'low' }} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('e.g. Nightly invoice sync'), {
      target: { value: 'Nightly sync' },
    });
    expect(lastArg(onChange)).toEqual({ priority: 'low', description: 'Nightly sync' });
  });

  test('changing priority merges into value', () => {
    const onChange = vi.fn();
    render(<AdvancedOptionsForm value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'high' } });
    expect(lastArg(onChange)).toEqual({ priority: 'high' });
  });

  test('changing max firings per hour coerces to a number', () => {
    const onChange = vi.fn();
    render(<AdvancedOptionsForm value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue('0'), { target: { value: '25' } });
    expect(lastArg(onChange)).toEqual({ max_firings_per_hour: 25 });
  });

  test('changing expires_at_iso merges into value', () => {
    const onChange = vi.fn();
    render(<AdvancedOptionsForm value={{}} onChange={onChange} />);
    const input = document.querySelector('input[type="datetime-local"]') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '2027-01-01T00:00' } });
    expect(lastArg(onChange)).toEqual({ expires_at_iso: '2027-01-01T00:00' });
  });

  test('editing the CEL condition merges into value', () => {
    const onChange = vi.fn();
    render(<AdvancedOptionsForm value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('payload.amount > 1000'), {
      target: { value: "payload.env == 'prod'" },
    });
    expect(lastArg(onChange)).toEqual({ condition: "payload.env == 'prod'" });
  });

  test('editing the failure notify target merges into value', () => {
    const onChange = vi.fn();
    render(<AdvancedOptionsForm value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('#ops-alerts or ops@acme.com'), {
      target: { value: '#ops-alerts' },
    });
    expect(lastArg(onChange)).toEqual({ on_failure_notify: '#ops-alerts' });
  });

  test('shows comma-joined tags and re-splits/trims/filters on edit', () => {
    const onChange = vi.fn();
    render(<AdvancedOptionsForm value={{ tags: ['ops', 'billing'] }} onChange={onChange} />);
    expect(screen.getByPlaceholderText('ops, billing')).toHaveValue('ops, billing');
    fireEvent.change(screen.getByPlaceholderText('ops, billing'), {
      target: { value: 'a, , b ,  ,c' },
    });
    expect(lastArg(onChange)).toEqual({ tags: ['a', 'b', 'c'] });
  });

  test('ignores a non-array tags value (renders empty)', () => {
    render(<AdvancedOptionsForm value={{ tags: 'not-an-array' }} onChange={vi.fn()} />);
    expect(screen.getByPlaceholderText('ops, billing')).toHaveValue('');
  });

  test('toggling simulation mode merges the checked state', () => {
    const onChange = vi.fn();
    render(<AdvancedOptionsForm value={{}} onChange={onChange} />);
    fireEvent.click(screen.getByRole('checkbox'));
    expect(lastArg(onChange)).toEqual({ simulation_mode: true });
  });

  test('shows a checked simulation mode checkbox when true', () => {
    render(<AdvancedOptionsForm value={{ simulation_mode: true }} onChange={vi.fn()} />);
    expect(screen.getByRole('checkbox')).toBeChecked();
  });
});
