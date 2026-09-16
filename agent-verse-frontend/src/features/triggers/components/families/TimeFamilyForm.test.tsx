import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { TimeFamilyForm } from './TimeFamilyForm';

/** Grab the object the component most recently handed to onChange. */
function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

afterEach(() => vi.restoreAllMocks());

describe('TimeFamilyForm', () => {
  test('cron renders the Cron Expression + Timezone (default UTC) and merges a timezone edit', () => {
    const onChange = vi.fn();
    render(<TimeFamilyForm triggerType="cron" value={{ description: 'x' }} onChange={onChange} />);
    expect(screen.getByText('Cron Expression')).toBeInTheDocument();
    expect(screen.getByText('Timezone')).toBeInTheDocument();
    // Timezone input defaults to UTC; editing it merges with existing value.
    fireEvent.change(screen.getByDisplayValue('UTC'), { target: { value: 'America/New_York' } });
    expect(lastArg(onChange)).toEqual({ description: 'x', timezone: 'America/New_York' });
  });

  test('interval defaults to 3600 and coerces the changed value to a number', () => {
    const onChange = vi.fn();
    render(<TimeFamilyForm triggerType="interval" value={{}} onChange={onChange} />);
    expect(screen.getByText('Interval (seconds)')).toBeInTheDocument();
    fireEvent.change(screen.getByDisplayValue('3600'), { target: { value: '900' } });
    expect(lastArg(onChange)).toEqual({ interval_seconds: 900 });
  });

  test('once renders Run At and writes fire_at_iso from the datetime-local input', () => {
    const onChange = vi.fn();
    const { container } = render(<TimeFamilyForm triggerType="once" value={{}} onChange={onChange} />);
    expect(screen.getByText('Run At (ISO 8601)')).toBeInTheDocument();
    const dt = container.querySelector('input[type="datetime-local"]')!;
    fireEvent.change(dt, { target: { value: '2026-01-01T09:30' } });
    expect(lastArg(onChange)).toEqual({ fire_at_iso: '2026-01-01T09:30' });
  });

  test('deadline renders its $.due_at placeholder and coerces the warning seconds to a number', () => {
    const onChange = vi.fn();
    render(<TimeFamilyForm triggerType="deadline" value={{}} onChange={onChange} />);
    expect(screen.getByPlaceholderText('$.due_at')).toBeInTheDocument();
    fireEvent.change(screen.getByDisplayValue('3600'), { target: { value: '600' } });
    expect(lastArg(onChange)).toEqual({ deadline_warning_seconds: 600 });
  });

  test('relative_delay merges relative_to_field text and numeric offset', () => {
    const onChange = vi.fn();
    render(<TimeFamilyForm triggerType="relative_delay" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('$.created_at'), { target: { value: '$.submitted_at' } });
    expect(lastArg(onChange)).toEqual({ relative_to_field: '$.submitted_at' });
    fireEvent.change(screen.getByDisplayValue('3600'), { target: { value: '120' } });
    expect(lastArg(onChange)).toEqual({ relative_offset_seconds: 120 });
  });

  test('business_calendar renders the us-holidays placeholder and Timezone default', () => {
    const onChange = vi.fn();
    render(<TimeFamilyForm triggerType="business_calendar" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('us-holidays'), { target: { value: 'nyse' } });
    expect(lastArg(onChange)).toEqual({ business_calendar_id: 'nyse' });
    expect(screen.getByDisplayValue('UTC')).toBeInTheDocument();
  });

  test('the shared Max Firings field is a numeric field on every type', () => {
    const onChange = vi.fn();
    render(<TimeFamilyForm triggerType="once" value={{}} onChange={onChange} />);
    expect(screen.getByText('Max Firings (0 = unlimited)')).toBeInTheDocument();
    // Defaults to 0; changing it produces a number, not a string.
    fireEvent.change(screen.getByDisplayValue('0'), { target: { value: '5' } });
    expect(lastArg(onChange)).toEqual({ max_firings_per_hour: 5 });
  });
});
