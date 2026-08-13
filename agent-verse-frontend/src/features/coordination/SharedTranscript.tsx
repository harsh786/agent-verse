import type { CoordinationMessage } from './types';

export function SharedTranscript({ messages }: { messages: CoordinationMessage[] }) {
  return (
    <section aria-labelledby="transcript-heading">
      <h2 id="transcript-heading" className="text-lg font-semibold">Shared transcript</h2>
      {messages.length === 0 ? <p className="mt-3 text-sm text-muted-foreground">No messages yet.</p> : (
        <ol className="mt-4 space-y-4">
          {messages.map((message, index) => (
            <li key={message.message_id ?? message.sequence ?? index} className="border-l-2 border-border pl-4">
              <div className="flex flex-wrap gap-2 font-mono text-xs text-muted-foreground">
                <span>#{message.sequence}</span><span>{message.sender_agent_id ?? 'system'}</span>
                <span>→ {(message.recipient_agent_ids ?? ['all']).join(', ')}</span>
                <span>{message.classification ?? 'internal'}</span><span>{message.trust_label ?? 'unlabelled'}</span>
              </div>
              <p className="mt-2 text-sm leading-6">{message.safe_content ?? message.artifact_reference ?? 'Event recorded'}</p>
              {message.citation_ids?.length ? <p className="mt-1 text-xs text-muted-foreground">Citations: {message.citation_ids.join(', ')}</p> : null}
              {message.compacts_from_sequence != null ? <p className="mt-1 text-xs text-muted-foreground">Compacts #{message.compacts_from_sequence}–#{message.compacts_to_sequence}</p> : null}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
