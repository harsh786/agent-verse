import { Send } from 'lucide-react';
import { FormEvent, useState } from 'react';
import { useGroupChat } from './useGroupChat';

export function GroupChatPanel({ sessionId }: { sessionId: string }) {
  const chat = useGroupChat(sessionId);
  const [draft, setDraft] = useState('');

  function submit(event: FormEvent) {
    event.preventDefault();
    if (chat.send(draft)) setDraft('');
  }

  return (
    <section aria-labelledby="group-chat-heading">
      <div className="flex items-center justify-between">
        <h2 id="group-chat-heading" className="text-lg font-semibold">Group chat</h2>
        <span aria-live="polite" className="font-mono text-xs text-muted-foreground">{chat.status}</span>
      </div>
      {chat.messages.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">No chat messages yet.</p>
      ) : (
        <ol className="mt-3 max-h-80 space-y-2 overflow-y-auto" aria-label="Group chat messages">
          {chat.messages.map((message) => (
            <li key={message.message_id ?? message.sequence} className="rounded-md border px-3 py-2 text-sm">
              <span className="font-mono text-xs text-muted-foreground">#{message.sequence} {message.sender_agent_id ?? 'unknown'}</span>
              <p className="mt-1">{message.safe_content ?? message.artifact_reference ?? ''}</p>
            </li>
          ))}
        </ol>
      )}
      {chat.error && <p role="alert" className="mt-2 text-sm text-red-600">Message rejected: {chat.error}</p>}
      <form onSubmit={submit} className="mt-3 flex gap-2">
        <label htmlFor="group-chat-input" className="sr-only">Message</label>
        <input
          id="group-chat-input"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          maxLength={16_000}
          placeholder="Message the session"
          className="min-w-0 flex-1 rounded-md border bg-background px-3 py-2 text-sm"
        />
        <button
          type="submit"
          disabled={chat.status !== 'live' || !draft.trim()}
          className="inline-flex min-h-11 min-w-11 items-center justify-center gap-2 rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-50"
        >
          <Send className="h-4 w-4" aria-hidden="true" /> Send
        </button>
      </form>
    </section>
  );
}
