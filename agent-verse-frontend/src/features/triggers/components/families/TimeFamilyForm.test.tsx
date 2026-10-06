import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
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
    // B1-9: the shown default is submitted (an untouched field used to 422).
    expect(onChange).toHaveBeenCalledWith({ interval_seconds: 3600 });
    fireEvent.change(screen.getByDisplayValue('3600'), { target: { value: '900' } });
    expect(lastArg(onChange)).toEqual({ interval_seconds: 900 });
  });

  test('a shown default is never written over a value the trigger already has', () => {
    const onChange = vi.fn();
    render(<TimeFamilyForm triggerType="interval" value={{ interval_seconds: 600 }} onChange={onChange} />);
    expect(onChange).not.toHaveBeenCalled();
  });

  test('once sends the picked local time as an explicit UTC instant (B1-9)', () => {
    const onChange = vi.fn();
    const { container } = render(<TimeFamilyForm triggerType="once" value={{}} onChange={onChange} />);
    expect(screen.getByText('Run At')).toBeInTheDocument();
    const dt = container.querySelector('input[type="datetime-local"]')!;
    fireEvent.change(dt, { target: { value: '2026-01-01T09:30' } });
    // A zone-less value was read as UTC by the backend; it now carries its zone.
    expect(lastArg(onChange)).toEqual({ fire_at_iso: new Date('2026-01-01T09:30').toISOString() });
    expect(String(lastArg(onChange).fire_at_iso)).toMatch(/Z$/);
  });

  test('a stored time is shown back in local time, a zone-less one read as UTC', () => {
    const { container } = render(
      <TimeFamilyForm triggerType="once" value={{ fire_at_iso: '2026-01-01T04:00:00Z' }} onChange={vi.fn()} />,
    );
    const shown = (container.querySelector('input[type="datetime-local"]') as HTMLInputElement).value;
    expect(new Date(shown).toISOString()).toBe('2026-01-01T04:00:00.000Z');
    const { container: legacy } = render(
      <TimeFamilyForm triggerType="once" value={{ fire_at_iso: '2026-01-01T04:00' }} onChange={vi.fn()} />,
    );
    const legacyShown = (legacy.querySelector('input[type="datetime-local"]') as HTMLInputElement).value;
    expect(new Date(legacyShown).toISOString()).toBe('2026-01-01T04:00:00.000Z');
  });

  test('deadline asks for the deadline time (fire_at_iso) and warn-before seconds (TRG-08)', () => {
    const onChange = vi.fn();
    const { container } = render(<TimeFamilyForm triggerType="deadline" value={{}} onChange={onChange} />);
    // Payload deadlines (deadline_field) never fired; the field is gone.
    expect(screen.queryByPlaceholderText('$.due_at')).not.toBeInTheDocument();
    const dt = container.querySelector('input[type="datetime-local"]')!;
    expect(dt).toBeRequired();
    expect(onChange).toHaveBeenCalledWith({ deadline_warning_seconds: 3600 });
    fireEvent.change(dt, { target: { value: '2026-10-01T09:00' } });
    expect(lastArg(onChange)).toEqual({ fire_at_iso: new Date('2026-10-01T09:00').toISOString() });
    fireEvent.change(screen.getByDisplayValue('3600'), { target: { value: '600' } });
    expect(lastArg(onChange)).toEqual({ deadline_warning_seconds: 600 });
  });

  test('relative_delay asks for a base time plus a numeric offset (TRG-08)', () => {
    const onChange = vi.fn();
    const { container } = render(<TimeFamilyForm triggerType="relative_delay" value={{}} onChange={onChange} />);
    expect(screen.queryByPlaceholderText('$.created_at')).not.toBeInTheDocument();
    expect(screen.getByText('Base Time')).toBeInTheDocument();
    const dt = container.querySelector('input[type="datetime-local"]')!;
    expect(dt).toBeRequired();
    fireEvent.change(dt, { target: { value: '2026-10-01T09:00' } });
    expect(lastArg(onChange)).toEqual({ fire_at_iso: new Date('2026-10-01T09:00').toISOString() });
    fireEvent.change(screen.getByDisplayValue('3600'), { target: { value: '120' } });
    expect(lastArg(onChange)).toEqual({ relative_offset_seconds: 120 });
  });

  test('business_calendar asks for a cron expression, not a calendar id (TRG-08)', () => {
    const onChange = vi.fn();
    render(<TimeFamilyForm triggerType="business_calendar" value={{}} onChange={onChange} />);
    expect(screen.queryByPlaceholderText('us-holidays')).not.toBeInTheDocument();
    const cron = screen.getByPlaceholderText('0 9 * * 1-5');
    expect(cron).toBeRequired();
    fireEvent.change(cron, { target: { value: '0 10 * * *' } });
    expect(lastArg(onChange)).toEqual({ cron_expression: '0 10 * * *' });
    expect(screen.getByDisplayValue('UTC')).toBeInTheDocument();
  });

  test("cron and interval show the plan's minimum interval (TRG-16)", () => {
    useAuthStore.setState({ plan: 'free' });
    const { unmount } = render(<TimeFamilyForm triggerType="cron" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText(/free plan runs a schedule at most every 15 min/i)).toBeInTheDocument();
    unmount();
    useAuthStore.setState({ plan: 'enterprise' });
    render(<TimeFamilyForm triggerType="interval" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText(/enterprise plan runs a schedule at most every 1 min/i)).toBeInTheDocument();
  });

  test('the shared Max Firings field is a numeric field on every type', () => {
    const onChange = vi.fn();
    render(<TimeFamilyForm triggerType="once" value={{}} onChange={onChange} />);
    expect(screen.getByText('Max Firings (0 = unlimited)')).toBeInTheDocument();
    // Defaults to 0; changing it produces a number, not a string.
    fireEvent.change(screen.getByDisplayValue('0'), { target: { value: '5' } });
    expect(lastArg(onChange)).toEqual({ max_firings_per_hour: 5 });
  });

  test('relative_delay can count from each event on a channel, optionally a payload field (B1-8)', () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <TimeFamilyForm triggerType="relative_delay" value={{ fire_at_iso: '2026-10-01T09:00:00Z' }} onChange={onChange} />,
    );
    fireEvent.change(screen.getByDisplayValue('A fixed time'), { target: { value: 'event' } });
    expect(lastArg(onChange)).toEqual({ event_channel: '' });
    rerender(<TimeFamilyForm triggerType="relative_delay" value={{ event_channel: '' }} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('support.escalated'), { target: { value: 'support.escalated' } });
    expect(lastArg(onChange)).toEqual({ event_channel: 'support.escalated' });
    fireEvent.change(screen.getByPlaceholderText('order.delivered_at'), { target: { value: 'ticket.opened_at' } });
    expect(lastArg(onChange)).toEqual({ event_channel: '', relative_to_field: 'ticket.opened_at' });
    // A fixed relative delay loaded from the API (event_channel "") stays fixed.
    rerender(
      <TimeFamilyForm
        triggerType="relative_delay"
        value={{ event_channel: '', fire_at_iso: '2026-10-01T09:00:00Z' }}
        onChange={onChange}
      />,
    );
    expect(screen.getByDisplayValue('A fixed time')).toBeInTheDocument();
  });

  test('business_calendar edits holidays, business days and hours (B1-6)', () => {
    const onChange = vi.fn();
    render(<TimeFamilyForm triggerType="business_calendar" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText(/2026-11-09/), { target: { value: '2026-11-09\n2026-12-25' } });
    expect(lastArg(onChange)).toEqual({ holidays: ['2026-11-09', '2026-12-25'] });
    fireEvent.click(screen.getByLabelText('Sat'));
    expect(lastArg(onChange)).toEqual({ business_days: [0, 1, 2, 3, 4, 5] });
    fireEvent.change(screen.getByDisplayValue('17:00'), { target: { value: '18:30' } });
    expect(lastArg(onChange)).toEqual({ business_hours_end: '18:30' });
  });

  test('the missed-runs policy is offered for the types it applies to (B1-5)', () => {
    const onChange = vi.fn();
    const { unmount } = render(<TimeFamilyForm triggerType="cron" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue(/every missed run/), { target: { value: 'latest' } });
    expect(lastArg(onChange)).toEqual({ catch_up: 'latest' });
    unmount();
    render(<TimeFamilyForm triggerType="interval" value={{}} onChange={vi.fn()} />);
    expect(screen.queryByText('Missed runs')).not.toBeInTheDocument();
  });
});
