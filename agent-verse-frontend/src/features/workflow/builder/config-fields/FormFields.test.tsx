import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import {
  TextAreaField,
  NumberField,
  ToggleField,
  FieldShell,
  fieldId,
} from './FormFields';

afterEach(() => vi.restoreAllMocks());

describe('fieldId', () => {
  test('slugifies the label with the default prefix', () => {
    expect(fieldId('Max Tokens')).toBe('cfg-max-tokens');
  });

  test('honours a custom prefix', () => {
    expect(fieldId('JSON output', 'cfg-toggle')).toBe('cfg-toggle-json-output');
  });
});

describe('FieldShell', () => {
  test('renders the label, action, children and description', () => {
    render(
      <FieldShell
        label="Timeout"
        description="Max wall-clock time"
        action={<button type="button">act</button>}
      >
        <input aria-label="inner" />
      </FieldShell>,
    );
    expect(screen.getByText('Timeout')).toBeInTheDocument();
    expect(screen.getByText('Max wall-clock time')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'act' })).toBeInTheDocument();
    expect(screen.getByLabelText('inner')).toBeInTheDocument();
  });
});

describe('TextAreaField', () => {
  test('shows the value and reports edits as a string', () => {
    const onChange = vi.fn();
    render(
      <TextAreaField label="Prompt" value="hello" onChange={onChange} description="tip" />,
    );
    const ta = screen.getByLabelText('Prompt');
    expect(ta).toHaveValue('hello');
    expect(screen.getByText('tip')).toBeInTheDocument();
    fireEvent.change(ta, { target: { value: 'world' } });
    expect(onChange).toHaveBeenCalledWith('world');
  });
});

describe('NumberField', () => {
  test('coerces a numeric edit to a number and empty to undefined', () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <NumberField label="Temperature" value={0.7} onChange={onChange} min={0} max={2} step={0.1} />,
    );
    const input = screen.getByLabelText('Temperature');
    expect(input).toHaveValue(0.7);
    expect(input).toHaveAttribute('min', '0');
    expect(input).toHaveAttribute('max', '2');
    expect(input).toHaveAttribute('step', '0.1');

    fireEvent.change(input, { target: { value: '5' } });
    expect(onChange).toHaveBeenLastCalledWith(5);

    rerender(<NumberField label="Temperature" value={5} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Temperature'), { target: { value: '' } });
    expect(onChange).toHaveBeenLastCalledWith(undefined);
  });

  test('renders an empty control when value is undefined', () => {
    render(<NumberField label="Retries" value={undefined} onChange={vi.fn()} />);
    expect(screen.getByLabelText('Retries')).toHaveValue(null);
  });
});

describe('ToggleField', () => {
  test('reflects the boolean value on the switch and toggles it', () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <ToggleField label="JSON output" value={false} onChange={onChange} description="force json" />,
    );
    const sw = screen.getByRole('switch');
    expect(sw).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByText('force json')).toBeInTheDocument();
    fireEvent.click(sw);
    expect(onChange).toHaveBeenCalledWith(true);

    rerender(<ToggleField label="JSON output" value={true} onChange={onChange} />);
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(screen.getByRole('switch'));
    expect(onChange).toHaveBeenLastCalledWith(false);
  });
});
