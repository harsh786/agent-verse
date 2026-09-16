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
