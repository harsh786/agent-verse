import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';
import { ChatSessionSettingsModal } from './ChatSessionSettingsModal';
import type { ChatSession } from './types/chat.types';

const SESSION: ChatSession = {
  id: 's1',
  tenant_id: 't1',
  title: 'My chat',
  pinned: false,
  ttl_days: null,
  system_prompt: 'Be concise.',
  agent_id: null,
  folder_id: null,
  show_reasoning: false,
  proactive_suggestions: true,
  preferred_model: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

describe('ChatSessionSettingsModal', () => {
  test('renders nothing when session is null', () => {
    const { container } = render(
      <ChatSessionSettingsModal session={null} onClose={vi.fn()} onSave={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  test('renders dialog with session values pre-filled', () => {
    render(<ChatSessionSettingsModal session={SESSION} onClose={vi.fn()} onSave={vi.fn()} />);
    expect(screen.getByRole('dialog', { name: /session settings/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/system prompt/i)).toHaveValue('Be concise.');
    expect(screen.getAllByRole('switch')).toHaveLength(2);
  });

  test('editing the system prompt textarea updates its value', async () => {
    render(<ChatSessionSettingsModal session={SESSION} onClose={vi.fn()} onSave={vi.fn()} />);
    const textarea = screen.getByLabelText(/system prompt/i);
    await userEvent.clear(textarea);
    await userEvent.type(textarea, 'New persona');
    expect(textarea).toHaveValue('New persona');
  });

  test('toggling "Show reasoning" switch flips aria-checked', async () => {
    render(<ChatSessionSettingsModal session={SESSION} onClose={vi.fn()} onSave={vi.fn()} />);
    const switches = screen.getAllByRole('switch');
    const showReasoningSwitch = switches[0];
    expect(showReasoningSwitch).toHaveAttribute('aria-checked', 'false');
    await userEvent.click(showReasoningSwitch);
    expect(showReasoningSwitch).toHaveAttribute('aria-checked', 'true');
    await userEvent.click(showReasoningSwitch);
    expect(showReasoningSwitch).toHaveAttribute('aria-checked', 'false');
  });

  test('toggling "Proactive suggestions" switch flips aria-checked', async () => {
    render(<ChatSessionSettingsModal session={SESSION} onClose={vi.fn()} onSave={vi.fn()} />);
    const switches = screen.getAllByRole('switch');
    const proactiveSwitch = switches[1];
    expect(proactiveSwitch).toHaveAttribute('aria-checked', 'true');
    await userEvent.click(proactiveSwitch);
    expect(proactiveSwitch).toHaveAttribute('aria-checked', 'false');
  });

  test('Save button calls onSave with the current form state and closes', async () => {
    const onSave = vi.fn();
    const onClose = vi.fn();
    render(<ChatSessionSettingsModal session={SESSION} onClose={onClose} onSave={onSave} />);

    const switches = screen.getAllByRole('switch');
    await userEvent.click(switches[0]); // show_reasoning -> true
    await userEvent.click(switches[1]); // proactive -> false

    const textarea = screen.getByLabelText(/system prompt/i);
    await userEvent.clear(textarea);
    await userEvent.type(textarea, 'Updated prompt');

    await userEvent.click(screen.getByRole('button', { name: /^save$/i }));

    expect(onSave).toHaveBeenCalledWith({
      system_prompt: 'Updated prompt',
      show_reasoning: true,
      proactive_suggestions: false,
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('Save with an empty system prompt sends undefined (falsy branch)', async () => {
    const onSave = vi.fn();
    render(<ChatSessionSettingsModal session={SESSION} onClose={vi.fn()} onSave={onSave} />);
    const textarea = screen.getByLabelText(/system prompt/i);
    await userEvent.clear(textarea);
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }));
    expect(onSave).toHaveBeenCalledWith({
      system_prompt: undefined,
      show_reasoning: false,
      proactive_suggestions: true,
    });
  });

  test('Cancel button calls onClose without saving', async () => {
    const onSave = vi.fn();
    const onClose = vi.fn();
    render(<ChatSessionSettingsModal session={SESSION} onClose={onClose} onSave={onSave} />);
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onSave).not.toHaveBeenCalled();
  });

  test('X close button calls onClose', async () => {
    const onClose = vi.fn();
    render(<ChatSessionSettingsModal session={SESSION} onClose={onClose} onSave={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', { name: /close/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('clicking the backdrop (overlay itself) calls onClose', () => {
    const onClose = vi.fn();
    render(<ChatSessionSettingsModal session={SESSION} onClose={onClose} onSave={vi.fn()} />);
    fireEvent.click(screen.getByRole('dialog'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('clicking inside the modal card does NOT call onClose', () => {
    const onClose = vi.fn();
    render(<ChatSessionSettingsModal session={SESSION} onClose={onClose} onSave={vi.fn()} />);
    fireEvent.click(screen.getByRole('heading', { name: /session settings/i }));
    expect(onClose).not.toHaveBeenCalled();
  });
});
