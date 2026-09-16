import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { CommunicationForm } from './CommunicationForm';

function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

afterEach(() => vi.restoreAllMocks());

describe('CommunicationForm', () => {
  test('renders bot token and channel id fields for slack', () => {
    render(<CommunicationForm sourceType="slack" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Bot Token')).toBeInTheDocument();
    expect(screen.getByText('Channel ID')).toBeInTheDocument();
    expect(screen.queryByText('IMAP Host')).not.toBeInTheDocument();
  });

  test('typing bot token for slack calls onChange with merged value', () => {
    const onChange = vi.fn();
    render(<CommunicationForm sourceType="slack" value={{ channel_id: 'C1' }} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('xoxb-...'), { target: { value: 'xoxb-123' } });
    expect(lastArg(onChange)).toEqual({ channel_id: 'C1', bot_token: 'xoxb-123' });
  });

  test('renders IMAP host, username, password fields for email', () => {
    render(<CommunicationForm sourceType="email" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('IMAP Host')).toBeInTheDocument();
    expect(screen.getByText('Username')).toBeInTheDocument();
    expect(screen.getByText('Password')).toBeInTheDocument();
  });

  test('typing IMAP host for email calls onChange', () => {
    const onChange = vi.fn();
    render(<CommunicationForm sourceType="email" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('imap.gmail.com'), { target: { value: 'imap.acme.com' } });
    expect(lastArg(onChange)).toEqual({ imap_host: 'imap.acme.com' });
  });

  test('renders webhook URL field for teams', () => {
    const onChange = vi.fn();
    render(<CommunicationForm sourceType="teams" value={{}} onChange={onChange} />);
    expect(screen.getByText('Webhook URL')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('https://...'), { target: { value: 'https://hooks.example.com' } });
    expect(lastArg(onChange)).toEqual({ webhook_url: 'https://hooks.example.com' });
  });

  test('renders bot token field for discord', () => {
    const onChange = vi.fn();
    render(<CommunicationForm sourceType="discord" value={{ x: 1 }} onChange={onChange} />);
    expect(screen.getByText('Bot Token')).toBeInTheDocument();
    // discord's bot token input has no placeholder, so query by label association via container
    const input = screen.getByText('Bot Token').parentElement?.querySelector('input');
    expect(input).toBeTruthy();
    fireEvent.change(input as HTMLInputElement, { target: { value: 'tok' } });
    expect(lastArg(onChange)).toEqual({ x: 1, bot_token: 'tok' });
  });

  test('renders nothing for an unknown source type', () => {
    render(<CommunicationForm sourceType="unknown_type" value={{}} onChange={vi.fn()} />);
    expect(screen.queryByText('Bot Token')).not.toBeInTheDocument();
    expect(screen.queryByText('IMAP Host')).not.toBeInTheDocument();
    expect(screen.queryByText('Webhook URL')).not.toBeInTheDocument();
  });
});
