/** Phase 7 — Composer: regenerate, attach + chips, slash menu, @-mentions, voice. */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { ChatInput } from './ChatInput';

afterEach(() => {
  // Ensure SpeechRecognition feature-detect stays undefined between tests.
  delete (window as unknown as Record<string, unknown>).webkitSpeechRecognition;
  delete (window as unknown as Record<string, unknown>).SpeechRecognition;
});

describe('ChatInput — regenerate', () => {
  it('renders a Regenerate control that calls onRegenerate', () => {
    const onRegenerate = vi.fn();
    render(
      <ChatInput onSend={vi.fn()} isLoading={false} onRegenerate={onRegenerate} canRegenerate />,
    );
    fireEvent.click(screen.getByTestId('regenerate-button'));
    expect(onRegenerate).toHaveBeenCalledTimes(1);
  });

  it('hides Regenerate when there is nothing to regenerate', () => {
    render(<ChatInput onSend={vi.fn()} isLoading={false} onRegenerate={vi.fn()} canRegenerate={false} />);
    expect(screen.queryByTestId('regenerate-button')).toBeNull();
  });
});

describe('ChatInput — file attach', () => {
  it('uploads a dropped/selected file, calls upload, and shows a chip', async () => {
    const onUploadAttachment = vi
      .fn()
      .mockResolvedValue({ attachment_id: 'a1', filename: 'doc.pdf', content_type: 'application/pdf', size: 10 });
    render(<ChatInput onSend={vi.fn()} isLoading={false} onUploadAttachment={onUploadAttachment} />);

    const file = new File(['x'], 'doc.pdf', { type: 'application/pdf' });
    fireEvent.change(screen.getByTestId('file-input'), { target: { files: [file] } });

    await waitFor(() => expect(onUploadAttachment).toHaveBeenCalledWith(file));
    expect(await screen.findByText('doc.pdf')).toBeDefined();
  });

  it('hides the attach button when no upload handler is provided', () => {
    render(<ChatInput onSend={vi.fn()} isLoading={false} />);
    expect(screen.queryByTestId('attach-button')).toBeNull();
  });
});

describe('ChatInput — slash commands', () => {
  it('opens the slash menu on "/" and filters as you type', () => {
    const onSlashCommand = vi.fn();
    render(<ChatInput onSend={vi.fn()} isLoading={false} onSlashCommand={onSlashCommand} />);
    const ta = screen.getByLabelText('Chat message input');

    fireEvent.change(ta, { target: { value: '/' } });
    expect(screen.getByTestId('slash-menu')).toBeDefined();
    expect(screen.getByText('/clear')).toBeDefined();
    expect(screen.getByText('/model')).toBeDefined();

    fireEvent.change(ta, { target: { value: '/cl' } });
    expect(screen.getByText('/clear')).toBeDefined();
    expect(screen.queryByText('/model')).toBeNull();
  });

  it('triggers onSlashCommand for /clear', () => {
    const onSlashCommand = vi.fn();
    render(<ChatInput onSend={vi.fn()} isLoading={false} onSlashCommand={onSlashCommand} />);
    fireEvent.change(screen.getByLabelText('Chat message input'), { target: { value: '/clear' } });
    fireEvent.click(within(screen.getByTestId('slash-menu')).getByText('/clear'));
    expect(onSlashCommand).toHaveBeenCalledWith('/clear');
  });
});

describe('ChatInput — @-mentions', () => {
  it('opens the mention menu on "@" and inserts the selection', () => {
    render(
      <ChatInput onSend={vi.fn()} isLoading={false} mentionOptions={['github', 'slack', 'planner']} />,
    );
    const ta = screen.getByLabelText('Chat message input') as HTMLTextAreaElement;

    fireEvent.change(ta, { target: { value: '@' } });
    expect(screen.getByTestId('mention-menu')).toBeDefined();
    expect(screen.getByText('@github')).toBeDefined();

    fireEvent.change(ta, { target: { value: '@sl' } });
    expect(screen.getByText('@slack')).toBeDefined();
    expect(screen.queryByText('@github')).toBeNull();

    fireEvent.click(screen.getByText('@slack'));
    expect(ta.value).toContain('@slack');
  });
});

describe('ChatInput — voice input', () => {
  it('hides the mic button when SpeechRecognition is undefined', () => {
    render(<ChatInput onSend={vi.fn()} isLoading={false} />);
    expect(screen.queryByTestId('mic-button')).toBeNull();
  });

  it('shows the mic button when webkitSpeechRecognition exists', () => {
    (window as unknown as Record<string, unknown>).webkitSpeechRecognition = class {
      start() {}
      stop() {}
      onresult = null;
      onend = null;
      onerror = null;
      lang = '';
      interimResults = false;
      continuous = false;
    };
    render(<ChatInput onSend={vi.fn()} isLoading={false} />);
    expect(screen.getByTestId('mic-button')).toBeDefined();
  });
});
