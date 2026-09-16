import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { StreamingForm } from './StreamingForm';

function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

afterEach(() => vi.restoreAllMocks());

describe('StreamingForm', () => {
  test('always renders the topic field', () => {
    render(<StreamingForm sourceType="unknown" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Topic')).toBeInTheDocument();
  });

  test('renders bootstrap servers field for kafka and redpanda only', () => {
    const { rerender } = render(<StreamingForm sourceType="kafka" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Bootstrap Servers')).toBeInTheDocument();
    rerender(<StreamingForm sourceType="redpanda" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Bootstrap Servers')).toBeInTheDocument();
    rerender(<StreamingForm sourceType="kinesis" value={{}} onChange={vi.fn()} />);
    expect(screen.queryByText('Bootstrap Servers')).not.toBeInTheDocument();
  });

  test('renders stream name field only for kinesis', () => {
    render(<StreamingForm sourceType="kinesis" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Stream Name')).toBeInTheDocument();
  });

  test('renders subscription field only for pubsub', () => {
    render(<StreamingForm sourceType="pubsub" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Subscription')).toBeInTheDocument();
  });

  test('renders event bus field only for eventbridge', () => {
    render(<StreamingForm sourceType="eventbridge" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Event Bus')).toBeInTheDocument();
  });

  test('typing bootstrap servers calls onChange with merged value', () => {
    const onChange = vi.fn();
    render(<StreamingForm sourceType="kafka" value={{ topic: 't1' }} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('localhost:9092'), { target: { value: 'broker:9092' } });
    expect(lastArg(onChange)).toEqual({ topic: 't1', bootstrap_servers: 'broker:9092' });
  });

  test('typing topic calls onChange', () => {
    const onChange = vi.fn();
    render(<StreamingForm sourceType="kinesis" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('my-topic'), { target: { value: 'orders' } });
    expect(lastArg(onChange)).toEqual({ topic: 'orders' });
  });

  test('does not render kinesis/pubsub/eventbridge fields for kafka', () => {
    render(<StreamingForm sourceType="kafka" value={{}} onChange={vi.fn()} />);
    expect(screen.queryByText('Stream Name')).not.toBeInTheDocument();
    expect(screen.queryByText('Subscription')).not.toBeInTheDocument();
    expect(screen.queryByText('Event Bus')).not.toBeInTheDocument();
  });
});
