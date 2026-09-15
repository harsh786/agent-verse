/** Phase 7 — channel origin/continuation badge. */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ChatChannelBadge } from './ChatChannelBadge';
import { ChatMessage } from './ChatMessage';
import type { ChatMessage as ChatMessageType } from './types/chat.types';

describe('ChatChannelBadge', () => {
  it('renders a friendly "via <Channel>" label for a known channel', () => {
    render(<ChatChannelBadge channel="whatsapp" />);
    expect(screen.getByText(/via WhatsApp/)).toBeDefined();
  });

  it('falls back to a capitalized label for an unknown channel', () => {
    render(<ChatChannelBadge channel="matrix" />);
    expect(screen.getByText(/via Matrix/)).toBeDefined();
  });
});

describe('ChatMessage channel metadata', () => {
  const base: ChatMessageType = {
    id: 'm1',
    session_id: 's1',
    role: 'user',
    content: 'hello from my phone',
    metadata: { channel: 'telegram' },
    intent: null,
    goal_id: null,
    branch_id: null,
    parent_message_id: null,
    created_at: new Date().toISOString(),
  };

  it('shows the channel badge when message.metadata.channel is present', () => {
    render(<ChatMessage message={base} />);
    expect(screen.getByText(/via Telegram/)).toBeDefined();
  });

  it('renders no channel badge when metadata has no channel', () => {
    render(<ChatMessage message={{ ...base, metadata: {} }} />);
    expect(screen.queryByTestId('chat-channel-badge')).toBeNull();
  });
});
