import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { WebhookFamilyForm } from './WebhookFamilyForm';

/** Grab the object the component most recently handed to onChange. */
function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

afterEach(() => vi.restoreAllMocks());

describe('WebhookFamilyForm', () => {
  test('webhook renders Endpoint Name (-> description) plus the shared secret + keys + CEL fields', () => {
    const onChange = vi.fn();
    render(<WebhookFamilyForm triggerType="webhook" value={{}} onChange={onChange} />);
    expect(screen.getByText('Endpoint Name')).toBeInTheDocument();
    expect(screen.getByText('Webhook Secret')).toBeInTheDocument();
    expect(screen.getByText('Allowed API Keys')).toBeInTheDocument();
    expect(screen.getByText('Condition CEL')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('my-webhook'), { target: { value: 'orders-hook' } });
    expect(lastArg(onChange)).toEqual({ description: 'orders-hook' });
  });

  test('the webhook secret writes to webhook_signature_secret via the password input', () => {
    const onChange = vi.fn();
    render(<WebhookFamilyForm triggerType="webhook" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('whsec_…'), { target: { value: 'whsec_topsecret' } });
    expect(lastArg(onChange)).toEqual({ webhook_signature_secret: 'whsec_topsecret' });
  });

  test('Allowed API Keys splits a comma string into trimmed, non-empty entries', () => {
    const onChange = vi.fn();
    render(<WebhookFamilyForm triggerType="webhook" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('key_live_abc, key_live_def'), {
      target: { value: 'key_live_abc, key_live_def ,' },
    });
    expect(lastArg(onChange)).toEqual({ allowed_api_keys: ['key_live_abc', 'key_live_def'] });
  });

  test('a pre-populated allowed_api_keys array renders joined by ", " in the input', () => {
    render(
      <WebhookFamilyForm
        triggerType="webhook"
        value={{ allowed_api_keys: ['key_live_abc', 'key_live_def'] }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByDisplayValue('key_live_abc, key_live_def')).toBeInTheDocument();
  });

  test('jira_webhook renders its project filter (-> jira_project_filter)', () => {
    const onChange = vi.fn();
    render(<WebhookFamilyForm triggerType="jira_webhook" value={{}} onChange={onChange} />);
    expect(screen.getByText('Jira Project Filter')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('OPS'), { target: { value: 'ENG' } });
    expect(lastArg(onChange)).toEqual({ jira_project_filter: 'ENG' });
  });

  test('event renders both the channel and JSONPath filter fields', () => {
    const onChange = vi.fn();
    render(<WebhookFamilyForm triggerType="event" value={{}} onChange={onChange} />);
    expect(screen.getByText('Event Channel')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('orders.created'), { target: { value: 'invoices.paid' } });
    expect(lastArg(onChange)).toEqual({ event_channel: 'invoices.paid' });
    fireEvent.change(screen.getByPlaceholderText('$.type'), { target: { value: '$.kind' } });
    expect(lastArg(onChange)).toEqual({ event_filter: '$.kind' });
  });

  test('salesforce_event, github_webhook and stripe_webhook each render their own filter field', () => {
    const sf = vi.fn();
    const { unmount } = render(<WebhookFamilyForm triggerType="salesforce_event" value={{}} onChange={sf} />);
    fireEvent.change(screen.getByPlaceholderText('Opportunity'), { target: { value: 'Case' } });
    expect(lastArg(sf)).toEqual({ salesforce_object: 'Case' });
    unmount();

    const gh = vi.fn();
    const { unmount: u2 } = render(<WebhookFamilyForm triggerType="github_webhook" value={{}} onChange={gh} />);
    fireEvent.change(screen.getByPlaceholderText('push'), { target: { value: 'pull_request' } });
    expect(lastArg(gh)).toEqual({ github_event_filter: 'pull_request' });
    u2();

    const st = vi.fn();
    render(<WebhookFamilyForm triggerType="stripe_webhook" value={{}} onChange={st} />);
    fireEvent.change(screen.getByPlaceholderText('payment_intent.succeeded'), {
      target: { value: 'charge.refunded' },
    });
    expect(lastArg(st)).toEqual({ stripe_event_filter: 'charge.refunded' });
  });
});
