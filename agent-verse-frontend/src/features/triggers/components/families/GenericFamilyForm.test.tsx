import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { GenericFamilyForm } from './GenericFamilyForm';

/** Grab the object the component most recently handed to onChange. */
function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

afterEach(() => vi.restoreAllMocks());

describe('GenericFamilyForm', () => {
  test('renders the trigger type banner and both fields', () => {
    render(<GenericFamilyForm triggerType={'iot' as never} value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('iot')).toBeInTheDocument();
    expect(screen.getByText('Condition CEL (optional)')).toBeInTheDocument();
    expect(screen.getByText('Extra configuration (JSON)')).toBeInTheDocument();
  });

  test('shows the passed condition and raw JSON values', () => {
    render(
      <GenericFamilyForm
        triggerType={'iot' as never}
        value={{ condition: 'payload.temp > 30', _raw_extra: '{"a":1}' }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByDisplayValue('payload.temp > 30')).toBeInTheDocument();
    expect(screen.getByDisplayValue('{"a":1}')).toBeInTheDocument();
  });

  test('editing the condition calls onChange with the merged spec', () => {
    const onChange = vi.fn();
    render(<GenericFamilyForm triggerType={'iot' as never} value={{ description: 'x' }} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('payload.value > 100'), {
      target: { value: 'payload.ok == true' },
    });
    expect(lastArg(onChange)).toEqual({ description: 'x', condition: 'payload.ok == true' });
  });

  test('valid JSON in the extra field is parsed and merged into the spec', () => {
    const onChange = vi.fn();
    render(<GenericFamilyForm triggerType={'iot' as never} value={{ condition: 'c' }} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('{"mqtt_topic": "sensors/+/temp"}'), {
      target: { value: '{"mqtt_topic":"sensors/+/temp"}' },
    });
    // Parsed keys are spread in alongside the preserved condition + raw string.
    expect(lastArg(onChange)).toEqual({
      condition: 'c',
      mqtt_topic: 'sensors/+/temp',
      _raw_extra: '{"mqtt_topic":"sensors/+/temp"}',
    });
  });

  test('invalid JSON only records the raw string without throwing', () => {
    const onChange = vi.fn();
    render(<GenericFamilyForm triggerType={'iot' as never} value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('{"mqtt_topic": "sensors/+/temp"}'), {
      target: { value: '{not valid json' },
    });
    // Only the _raw_extra passthrough fires; the parse branch is skipped.
    expect(lastArg(onChange)).toEqual({ _raw_extra: '{not valid json' });
  });
});
