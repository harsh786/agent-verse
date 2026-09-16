import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { PollingFamilyForm } from './PollingFamilyForm';

/** Grab the object the component most recently handed to onChange. */
function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

afterEach(() => vi.restoreAllMocks());

describe('PollingFamilyForm', () => {
  test('graphql_subscription renders the endpoint, subscription query and Kafka topic fields', () => {
    render(<PollingFamilyForm triggerType="graphql_subscription" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('GraphQL Endpoint (WebSocket)')).toBeInTheDocument();
    expect(screen.getByText('Subscription Query')).toBeInTheDocument();
    expect(screen.getByText('Kafka Topic')).toBeInTheDocument();
  });

  test('graphql endpoint edit merges into graphql_endpoint', () => {
    const onChange = vi.fn();
    render(<PollingFamilyForm triggerType="graphql_subscription" value={{ description: 'x' }} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('wss://api.example.com/graphql'), {
      target: { value: 'wss://live.example.com/graphql' },
    });
    expect(lastArg(onChange)).toEqual({ description: 'x', graphql_endpoint: 'wss://live.example.com/graphql' });
  });

  test('the subscription query textarea writes graphql_subscription_query', () => {
    const onChange = vi.fn();
    render(<PollingFamilyForm triggerType="graphql_subscription" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('subscription { onOrderCreated { id status } }'), {
      target: { value: 'subscription { onPaid { id } }' },
    });
    expect(lastArg(onChange)).toEqual({ graphql_subscription_query: 'subscription { onPaid { id } }' });
  });

  test('price_threshold upper-cases the symbol into price_symbol', () => {
    const onChange = vi.fn();
    render(<PollingFamilyForm triggerType="price_threshold" value={{}} onChange={onChange} />);
    expect(screen.getByText('Symbol')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('BTC'), { target: { value: 'btc' } });
    expect(lastArg(onChange)).toEqual({ price_symbol: 'BTC' });
  });

  test('price_threshold coerces the threshold price to a number', () => {
    const onChange = vi.fn();
    render(<PollingFamilyForm triggerType="price_threshold" value={{}} onChange={onChange} />);
    expect(screen.getByText('Threshold Price')).toBeInTheDocument();
    // Threshold defaults to 0; change it and assert a numeric payload.
    fireEvent.change(screen.getByDisplayValue('0'), { target: { value: '42000.5' } });
    expect(lastArg(onChange)).toEqual({ price_threshold: 42000.5 });
  });

  test('the direction select offers above/below/either and updates price_direction', () => {
    const onChange = vi.fn();
    render(<PollingFamilyForm triggerType="price_threshold" value={{}} onChange={onChange} />);
    // Defaults to "above".
    const select = screen.getByDisplayValue('Crosses above') as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual(['above', 'below', 'either']);
    fireEvent.change(select, { target: { value: 'below' } });
    expect(lastArg(onChange)).toEqual({ price_direction: 'below' });
  });
});
