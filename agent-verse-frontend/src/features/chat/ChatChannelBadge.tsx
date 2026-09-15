/**
 * ChatChannelBadge — small "via WhatsApp/Telegram/…" origin badge.
 *
 * Rendered on a message (or session) when channel metadata is present, so a
 * conversation that originated or continued on an external channel is visibly
 * marked in the web UI.
 */

import { type JSX } from 'react';

interface Props {
  channel: string;
  className?: string;
}

const CHANNEL_LABELS: Record<string, { label: string; icon: string; className: string }> = {
  whatsapp: { label: 'WhatsApp', icon: '💬', className: 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300' },
  telegram: { label: 'Telegram', icon: '✈️', className: 'bg-sky-100 text-sky-700 dark:bg-sky-900 dark:text-sky-300' },
  slack: { label: 'Slack', icon: '#️⃣', className: 'bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300' },
  email: { label: 'Email', icon: '✉️', className: 'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300' },
  sms: { label: 'SMS', icon: '📱', className: 'bg-gray-200 text-gray-700 dark:bg-gray-700 dark:text-gray-200' },
  web: { label: 'Web', icon: '🌐', className: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900 dark:text-indigo-300' },
};

export function ChatChannelBadge({ channel, className = '' }: Props): JSX.Element {
  const key = channel.toLowerCase();
  const meta = CHANNEL_LABELS[key] ?? {
    label: channel.charAt(0).toUpperCase() + channel.slice(1),
    icon: '🔗',
    className: 'bg-gray-200 text-gray-700 dark:bg-gray-700 dark:text-gray-200',
  };

  return (
    <span
      className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-medium ${meta.className} ${className}`}
      data-testid="chat-channel-badge"
      aria-label={`via ${meta.label}`}
    >
      <span aria-hidden="true">{meta.icon}</span>
      via {meta.label}
    </span>
  );
}
