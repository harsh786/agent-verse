/**
 * mergeChatMessages — merge persisted (DB) messages with local optimistic ones,
 * de-duplicating the overlap.
 *
 * The chat UI shows `[...dbMessages, ...localMessages]`. When a streamed reply
 * (or an optimistic user turn) is later persisted and refetched, the same
 * logical message would otherwise appear twice — once from the local optimistic
 * copy and once from the DB. We drop a local message when the DB already
 * contains it, matched by id first and then by (role + trimmed content).
 *
 * The pending assistant placeholder (empty content) is always kept so the
 * streaming bubble stays visible until real content lands.
 */

import type { ChatMessage } from './types/chat.types';

const sig = (m: ChatMessage): string => `${m.role}::${m.content.trim()}`;

export function mergeChatMessages(
  dbMessages: ChatMessage[],
  localMessages: ChatMessage[],
): ChatMessage[] {
  const dbIds = new Set(dbMessages.map((m) => m.id));
  const dbSignatures = new Set(
    dbMessages.filter((m) => m.content.trim().length > 0).map(sig),
  );

  const dedupedLocal = localMessages.filter((m) => {
    if (dbIds.has(m.id)) return false; // same id already persisted
    if (m.content.trim().length > 0 && dbSignatures.has(sig(m))) return false; // same role+content
    return true;
  });

  return [...dbMessages, ...dedupedLocal];
}
