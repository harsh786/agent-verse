import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { ConversationalFamilyForm } from './ConversationalFamilyForm';

function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

afterEach(() => vi.restoreAllMocks());

describe('ConversationalFamilyForm', () => {
  test('chat_command renders the command pattern field plus channel restrictions', () => {
    render(<ConversationalFamilyForm triggerType="chat_command" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Command Pattern')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('/run')).toBeInTheDocument();
    // chat_command is in the channel-scoped group, so channel fields also show.
    expect(screen.getByText('Channel ID (optional)')).toBeInTheDocument();
  });

  test('chat_command edit reports command_pattern via onChange', () => {
    const onChange = vi.fn();
    render(<ConversationalFamilyForm triggerType="chat_command" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('/run'), { target: { value: '/deploy' } });
    expect(lastArg(onChange)).toEqual({ command_pattern: '/deploy' });
  });

  test('chat_keyword renders a keyword pattern field and shows its value', () => {
    render(
      <ConversationalFamilyForm
        triggerType="chat_keyword"
        value={{ keyword_pattern: 'urgent|alert' }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText('Keyword Pattern')).toBeInTheDocument();
    expect(screen.getByDisplayValue('urgent|alert')).toBeInTheDocument();
  });

  test('email_intent renders sender + subject filters and edits the subject', () => {
    const onChange = vi.fn();
    render(<ConversationalFamilyForm triggerType="email_intent" value={{}} onChange={onChange} />);
    expect(screen.getByPlaceholderText('@company.com')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('urgent|invoice|.*paid'), {
      target: { value: '.*invoice' },
    });
    expect(lastArg(onChange)).toEqual({ email_subject_pattern: '.*invoice' });
  });

  test('chat_mention renders keyword pattern + bot id fields and reports edits', () => {
    const onChange = vi.fn();
    render(<ConversationalFamilyForm triggerType="chat_mention" value={{}} onChange={onChange} />);
    expect(screen.getByText('Keyword Pattern')).toBeInTheDocument();
    expect(screen.getByText('Bot ID (optional)')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('U0BOTID'), { target: { value: 'U123' } });
    expect(lastArg(onChange)).toEqual({ mention_bot_id: 'U123' });
  });

  test('slack_event renders channel type + id fields and reports edits', () => {
    const onChange = vi.fn();
    render(<ConversationalFamilyForm triggerType="slack_event" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('public'), { target: { value: 'private' } });
    expect(lastArg(onChange)).toEqual({ channel_type: 'private' });
    fireEvent.change(screen.getByPlaceholderText('C1234ABCD'), { target: { value: 'C9999' } });
    expect(lastArg(onChange)).toEqual({ channel_id: 'C9999' });
  });

  test('email_arrival renders sender filter field and reports edits', () => {
    const onChange = vi.fn();
    render(<ConversationalFamilyForm triggerType="email_arrival" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('@company.com'), { target: { value: '@vendor.com' } });
    expect(lastArg(onChange)).toEqual({ email_sender_filter: '@vendor.com' });
  });

  test('sms_inbound renders phone filter field and reports edits', () => {
    const onChange = vi.fn();
    render(<ConversationalFamilyForm triggerType="sms_inbound" value={{}} onChange={onChange} />);
    expect(screen.getByText('Phone Number Filter (optional)')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('+1234567890'), { target: { value: '+15551234' } });
    expect(lastArg(onChange)).toEqual({ phone_number_filter: '+15551234' });
  });

  test('voice_transcript renders language field defaulted to en-US and reports edits', () => {
    const onChange = vi.fn();
    render(<ConversationalFamilyForm triggerType="voice_transcript" value={{}} onChange={onChange} />);
    expect(screen.getByDisplayValue('en-US')).toBeInTheDocument();
    fireEvent.change(screen.getByDisplayValue('en-US'), { target: { value: 'fr-FR' } });
    expect(lastArg(onChange)).toEqual({ voice_language: 'fr-FR' });
  });

  test('renders nothing for an unrelated trigger type', () => {
    const { container } = render(
      <ConversationalFamilyForm triggerType="cron" value={{}} onChange={vi.fn()} />,
    );
    expect(container.querySelector('.space-y-4')?.children.length).toBe(0);
  });

  test('meeting_ended platform select fires onChange with the platform', () => {
    const onChange = vi.fn();
    render(<ConversationalFamilyForm triggerType="meeting_ended" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'zoom' } });
    expect(lastArg(onChange)).toEqual({ meeting_platform: 'zoom' });
  });

  test('form_submission renders a single form-id field', () => {
    render(<ConversationalFamilyForm triggerType="form_submission" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Form ID')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('contact-form-001')).toBeInTheDocument();
  });
});
