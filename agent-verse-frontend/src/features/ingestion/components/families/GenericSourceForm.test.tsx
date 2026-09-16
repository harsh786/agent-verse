import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { GenericSourceForm } from './GenericSourceForm';

function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

afterEach(() => vi.restoreAllMocks());

describe('GenericSourceForm', () => {
  test('renders endpoint URL, auth header, and extra config fields regardless of sourceType', () => {
    render(<GenericSourceForm sourceType="anything" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Endpoint URL')).toBeInTheDocument();
    expect(screen.getByText('Authentication Header (optional)')).toBeInTheDocument();
    expect(screen.getByText('Extra Configuration (JSON)')).toBeInTheDocument();
  });

  test('extra configuration defaults to an empty JSON object', () => {
    render(<GenericSourceForm sourceType="anything" value={{}} onChange={vi.fn()} />);
    expect(screen.getByDisplayValue('{}')).toBeInTheDocument();
  });

  test('typing endpoint URL calls onChange with merged value', () => {
    const onChange = vi.fn();
    render(<GenericSourceForm sourceType="x" value={{ auth_header: 'Bearer y' }} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('https://api.example.com/data'), {
      target: { value: 'https://api.acme.com/data' },
    });
    expect(lastArg(onChange)).toEqual({ auth_header: 'Bearer y', endpoint_url: 'https://api.acme.com/data' });
  });

  test('typing auth header calls onChange', () => {
    const onChange = vi.fn();
    render(<GenericSourceForm sourceType="x" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('Bearer <token>'), { target: { value: 'Bearer abc' } });
    expect(lastArg(onChange)).toEqual({ auth_header: 'Bearer abc' });
  });

  test('editing extra configuration JSON textarea calls onChange with raw text', () => {
    const onChange = vi.fn();
    render(<GenericSourceForm sourceType="x" value={{}} onChange={onChange} />);
    const textarea = screen.getByDisplayValue('{}');
    fireEvent.change(textarea, { target: { value: '{"a":1}' } });
    expect(lastArg(onChange)).toEqual({ config_json: '{"a":1}' });
  });

  test('renders existing values', () => {
    render(<GenericSourceForm sourceType="x" value={{ endpoint_url: 'https://e', config_json: '{"k":true}' }} onChange={vi.fn()} />);
    expect(screen.getByDisplayValue('https://e')).toBeInTheDocument();
    expect(screen.getByDisplayValue('{"k":true}')).toBeInTheDocument();
  });
});
