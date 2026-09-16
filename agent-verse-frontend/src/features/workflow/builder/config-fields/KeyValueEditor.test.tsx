import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { KeyValueEditor } from './KeyValueEditor';

/** Grab the object the component most recently handed to onChange. */
function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

afterEach(() => vi.restoreAllMocks());

describe('KeyValueEditor', () => {
  test('renders existing dict entries as key/value rows', () => {
    render(<KeyValueEditor label="Headers" value={{ 'Content-Type': 'application/json' }} onChange={vi.fn()} />);
    expect(screen.getByDisplayValue('Content-Type')).toBeInTheDocument();
    expect(screen.getByDisplayValue('application/json')).toBeInTheDocument();
    expect(screen.queryByText('No entries yet.')).not.toBeInTheDocument();
  });

  test('shows an empty hint when there are no entries', () => {
    render(<KeyValueEditor label="Args" value={undefined} onChange={vi.fn()} />);
    expect(screen.getByText('No entries yet.')).toBeInTheDocument();
  });

  test('editing a value commits the merged dict (string preserved)', () => {
    const onChange = vi.fn();
    render(<KeyValueEditor label="Args" value={{ foo: 'bar' }} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Args value 1'), { target: { value: 'baz' } });
    expect(lastArg(onChange)).toEqual({ foo: 'baz' });
  });

  test('renaming a key rehomes the value under the new key', () => {
    const onChange = vi.fn();
    render(<KeyValueEditor label="Args" value={{ foo: 'bar' }} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Args key 1'), { target: { value: 'x' } });
    expect(lastArg(onChange)).toEqual({ x: 'bar' });
  });

  test('a JSON-literal value round-trips as its real type', () => {
    const onChange = vi.fn();
    render(<KeyValueEditor label="Args" value={{ n: '1' }} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Args value 1'), { target: { value: '42' } });
    expect(lastArg(onChange)).toEqual({ n: 42 });
  });

  test('removing a row commits an empty dict', () => {
    const onChange = vi.fn();
    render(<KeyValueEditor label="Args" value={{ foo: 'bar' }} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Remove Args row 1' }));
    expect(lastArg(onChange)).toEqual({});
  });

  test('Raw JSON toggle exposes an object textarea that commits on blur', () => {
    const onChange = vi.fn();
    render(<KeyValueEditor label="Args" value={{ foo: 'bar' }} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: /Raw JSON/i }));
    const ta = screen.getByRole('textbox');
    fireEvent.change(ta, { target: { value: '{"a":1}' } });
    fireEvent.blur(ta);
    expect(lastArg(onChange)).toEqual({ a: 1 });
    // Back in rows view, the new key is shown.
    expect(screen.getByDisplayValue('a')).toBeInTheDocument();
  });

  test('Raw JSON with invalid content surfaces an inline error and does not commit', () => {
    const onChange = vi.fn();
    render(<KeyValueEditor label="Args" value={{ foo: 'bar' }} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: /Raw JSON/i }));
    const ta = screen.getByRole('textbox');
    fireEvent.change(ta, { target: { value: '{not json' } });
    fireEvent.blur(ta);
    expect(screen.getByText('Invalid JSON.')).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });
});
