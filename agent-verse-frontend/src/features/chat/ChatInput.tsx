/**
 * ChatInput — multi-line textarea with send button, model selector, and
 * keyboard shortcut (Enter to send, Shift+Enter for newline).
 *
 * Phase 7 composer additions:
 *  - Regenerate: re-run the last user turn via onRegenerate.
 *  - File attach + drag-drop: upload via onUploadAttachment, show chips.
 *  - Slash-commands: "/" at the start opens a filtered command menu.
 *  - @-mentions: "@" opens a filtered connector/agent menu.
 *  - Voice input: webkitSpeechRecognition mic (feature-detected, hidden when absent).
 */

import { useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { Send, Loader2, Square, RotateCcw, Paperclip, Mic, X } from 'lucide-react';

export interface AttachmentChip {
  attachment_id: string;
  filename: string;
  content_type?: string;
  size?: number;
}

export interface SlashCommand {
  name: string; // includes leading slash, e.g. "/clear"
  description: string;
  /** When 'trigger', selecting invokes onSlashCommand; when 'insert', it fills the input. */
  kind: 'trigger' | 'insert';
}

const DEFAULT_SLASH_COMMANDS: SlashCommand[] = [
  { name: '/clear', description: 'Start a new / cleared session', kind: 'trigger' },
  { name: '/model', description: 'Switch the active model', kind: 'insert' },
  { name: '/schedule', description: 'Schedule this as a recurring task', kind: 'insert' },
];

const DEFAULT_MENTIONS = ['agent', 'planner', 'executor', 'verifier'];

// Feature-detect the browser SpeechRecognition API once at module load.
type SpeechRecognitionCtor = new () => {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  start: () => void;
  stop: () => void;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
};

function getSpeechRecognition(): SpeechRecognitionCtor | undefined {
  const w = window as unknown as {
    webkitSpeechRecognition?: SpeechRecognitionCtor;
    SpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.webkitSpeechRecognition ?? w.SpeechRecognition;
}

interface Props {
  onSend: (content: string, model?: string, attachments?: AttachmentChip[]) => void;
  isLoading: boolean;
  availableModels?: string[];
  selectedModel?: string;
  onModelChange?: (model: string) => void;
  disabled?: boolean;
  /** When streaming, surfaces a Stop button that calls this to cancel. */
  onStop?: () => void;
  /** Re-run the last user turn. When absent (or no prior turn), the control is hidden. */
  onRegenerate?: () => void;
  canRegenerate?: boolean;
  /** Uploads a file and returns a chip descriptor. Enables attach + drag-drop. */
  onUploadAttachment?: (file: File) => Promise<AttachmentChip>;
  /** Invoked when a 'trigger' slash-command (e.g. /clear) is selected. */
  onSlashCommand?: (command: string) => void;
  slashCommands?: SlashCommand[];
  /** Names shown in the @-mention menu (connectors / agents). */
  mentionOptions?: string[];
}

export function ChatInput({
  onSend,
  isLoading,
  availableModels = [],
  selectedModel,
  onModelChange,
  disabled,
  onStop,
  onRegenerate,
  canRegenerate,
  onUploadAttachment,
  onSlashCommand,
  slashCommands = DEFAULT_SLASH_COMMANDS,
  mentionOptions = DEFAULT_MENTIONS,
}: Props) {
  const [value, setValue] = useState('');
  const [attachments, setAttachments] = useState<AttachmentChip[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [isRecording, setIsRecording] = useState(false);
  const [menu, setMenu] = useState<{ kind: 'slash' | 'mention'; query: string } | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const recognitionRef = useRef<InstanceType<SpeechRecognitionCtor> | null>(null);

  const voiceSupported = useMemo(() => getSpeechRecognition() !== undefined, []);

  // Derive which menu (slash or mention) should be visible from the current value.
  const updateMenu = (next: string) => {
    if (next.startsWith('/') && !next.includes(' ')) {
      setMenu({ kind: 'slash', query: next });
      return;
    }
    const at = next.match(/@([\w-]*)$/);
    if (at) {
      setMenu({ kind: 'mention', query: at[1] });
      return;
    }
    setMenu(null);
  };

  const setInput = (next: string) => {
    setValue(next);
    updateMenu(next);
  };

  const filteredCommands = menu?.kind === 'slash'
    ? slashCommands.filter((c) => c.name.startsWith(menu.query))
    : [];
  const filteredMentions = menu?.kind === 'mention'
    ? mentionOptions.filter((m) => m.toLowerCase().startsWith(menu.query.toLowerCase()))
    : [];

  const handleSend = () => {
    const trimmed = value.trim();
    if ((!trimmed && attachments.length === 0) || isLoading || disabled) return;
    onSend(trimmed, selectedModel, attachments.length > 0 ? attachments : undefined);
    setValue('');
    setAttachments([]);
    setMenu(null);
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (menu && (filteredCommands.length > 0 || filteredMentions.length > 0)) {
      // Escape closes the menu without sending.
      if (e.key === 'Escape') {
        e.preventDefault();
        setMenu(null);
        return;
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleInput = () => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
  };

  const selectCommand = (cmd: SlashCommand) => {
    setMenu(null);
    if (cmd.kind === 'trigger') {
      setValue('');
      onSlashCommand?.(cmd.name);
    } else {
      setInput(`${cmd.name} `);
      textareaRef.current?.focus();
    }
  };

  const selectMention = (name: string) => {
    // Replace the trailing "@query" with "@name ".
    const next = value.replace(/@([\w-]*)$/, `@${name} `);
    setInput(next);
    setMenu(null);
    textareaRef.current?.focus();
  };

  const uploadFiles = async (files: FileList | File[]) => {
    if (!onUploadAttachment) return;
    setUploadError(null);
    for (const file of Array.from(files)) {
      try {
        const chip = await onUploadAttachment(file);
        setAttachments((prev) => [...prev, chip]);
      } catch (err) {
        setUploadError(err instanceof Error ? err.message : 'Upload failed');
      }
    }
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files.length > 0) void uploadFiles(e.dataTransfer.files);
  };

  const toggleRecording = () => {
    const Ctor = getSpeechRecognition();
    if (!Ctor) return;
    if (isRecording) {
      recognitionRef.current?.stop();
      return;
    }
    const rec = new Ctor();
    rec.lang = 'en-US';
    rec.interimResults = false;
    rec.continuous = false;
    rec.onresult = (event) => {
      let transcript = '';
      for (let i = 0; i < event.results.length; i++) {
        transcript += event.results[i][0].transcript;
      }
      setInput(value ? `${value} ${transcript}` : transcript);
    };
    rec.onend = () => setIsRecording(false);
    rec.onerror = () => setIsRecording(false);
    recognitionRef.current = rec;
    setIsRecording(true);
    rec.start();
  };

  return (
    <div
      className={`relative border-t border-border bg-card px-4 py-3 ${
        isDragging ? 'ring-2 ring-indigo-500 ring-inset' : ''
      }`}
      onDragOver={(e) => {
        if (!onUploadAttachment) return;
        e.preventDefault();
        setIsDragging(true);
      }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={onUploadAttachment ? handleDrop : undefined}
      data-testid="chat-composer"
    >
      {isDragging && (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-background/80 text-sm text-indigo-300">
          Drop files to attach
        </div>
      )}

      {/* Slash / mention menu */}
      {menu?.kind === 'slash' && filteredCommands.length > 0 && (
        <div
          className="absolute bottom-full left-4 mb-2 w-64 rounded-xl border border-border bg-background py-1 shadow-lg z-20"
          data-testid="slash-menu"
          role="listbox"
          aria-label="Slash commands"
        >
          {filteredCommands.map((c) => (
            <button
              key={c.name}
              type="button"
              role="option"
              aria-selected={false}
              className="flex w-full flex-col items-start px-3 py-1.5 text-left hover:bg-white/[0.06]"
              onClick={() => selectCommand(c)}
            >
              <span className="text-sm text-foreground">{c.name}</span>
              <span className="text-xs text-muted-foreground/70">{c.description}</span>
            </button>
          ))}
        </div>
      )}
      {menu?.kind === 'mention' && filteredMentions.length > 0 && (
        <div
          className="absolute bottom-full left-4 mb-2 w-56 rounded-xl border border-border bg-background py-1 shadow-lg z-20"
          data-testid="mention-menu"
          role="listbox"
          aria-label="Mentions"
        >
          {filteredMentions.map((m) => (
            <button
              key={m}
              type="button"
              role="option"
              aria-selected={false}
              className="flex w-full items-center px-3 py-1.5 text-left text-sm text-foreground hover:bg-white/[0.06]"
              onClick={() => selectMention(m)}
            >
              @{m}
            </button>
          ))}
        </div>
      )}

      {((availableModels.length > 0 && onModelChange) || (onRegenerate && canRegenerate)) && (
      <div className="mb-2 flex items-center gap-2">
        {availableModels.length > 0 && onModelChange && (
          <>
            <label htmlFor="model-selector" className="text-xs text-muted-foreground/70">
              Model:
            </label>
            <select
              id="model-selector"
              className="text-xs border border-border rounded-lg px-2 py-1 bg-card text-muted-foreground"
              value={selectedModel ?? ''}
              onChange={(e) => onModelChange(e.target.value)}
              aria-label="Select LLM model"
            >
              {availableModels.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </>
        )}

        {onRegenerate && canRegenerate && (
          <button
            type="button"
            className="ml-auto flex items-center gap-1 text-xs text-muted-foreground hover:text-indigo-400 disabled:opacity-50 transition-colors"
            onClick={onRegenerate}
            disabled={isLoading || disabled}
            aria-label="Regenerate response"
            data-testid="regenerate-button"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            Regenerate
          </button>
        )}
      </div>
      )}

      {/* Attached-file chips */}
      {attachments.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-2" data-testid="attachment-chips">
          {attachments.map((a) => (
            <span
              key={a.attachment_id}
              className="inline-flex items-center gap-1 rounded-lg bg-secondary px-2 py-1 text-xs text-muted-foreground"
            >
              <Paperclip className="w-3 h-3" />
              {a.filename}
              <button
                type="button"
                onClick={() =>
                  setAttachments((prev) => prev.filter((p) => p.attachment_id !== a.attachment_id))
                }
                aria-label={`Remove ${a.filename}`}
                className="ml-0.5 hover:text-red-400"
              >
                <X className="w-3 h-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      {uploadError && (
        <p className="mb-2 text-xs text-red-400" role="alert">
          {uploadError}
        </p>
      )}

      <div className="flex items-end gap-3">
        {onUploadAttachment && (
          <>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              data-testid="file-input"
              aria-hidden="true"
              onChange={(e) => {
                if (e.target.files && e.target.files.length > 0) void uploadFiles(e.target.files);
                e.target.value = '';
              }}
            />
            <button
              type="button"
              className="shrink-0 w-11 h-11 rounded-xl bg-background hover:bg-secondary border border-border flex items-center justify-center text-muted-foreground transition-colors"
              onClick={() => fileInputRef.current?.click()}
              disabled={isLoading || disabled}
              aria-label="Attach file"
              data-testid="attach-button"
            >
              <Paperclip className="w-5 h-5" />
            </button>
          </>
        )}

        <textarea
          ref={textareaRef}
          rows={1}
          className="flex-1 resize-none rounded-xl border border-border bg-background px-4 py-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-indigo-500 min-h-[44px] max-h-[200px]"
          placeholder="Ask a question or describe a goal… (Enter to send, Shift+Enter for newline)"
          value={value}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          onInput={handleInput}
          disabled={isLoading || disabled}
          aria-label="Chat message input"
          aria-multiline="true"
        />

        {voiceSupported && (
          <button
            type="button"
            className={`shrink-0 w-11 h-11 rounded-xl border border-border flex items-center justify-center transition-colors ${
              isRecording
                ? 'bg-red-600 text-white'
                : 'bg-background hover:bg-secondary text-muted-foreground'
            }`}
            onClick={toggleRecording}
            disabled={isLoading || disabled}
            aria-label={isRecording ? 'Stop recording' : 'Start voice input'}
            data-testid="mic-button"
          >
            <Mic className="w-5 h-5" />
          </button>
        )}

        {isLoading && onStop ? (
          <button
            className="shrink-0 w-11 h-11 rounded-xl bg-red-600 hover:bg-red-700 flex items-center justify-center text-white transition-colors"
            onClick={onStop}
            aria-label="Stop generating"
            data-testid="stop-button"
          >
            <Square className="w-4 h-4" fill="currentColor" />
          </button>
        ) : (
          <button
            className="shrink-0 w-11 h-11 rounded-xl bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center text-white transition-colors"
            onClick={handleSend}
            disabled={(!value.trim() && attachments.length === 0) || isLoading || disabled}
            aria-label="Send message"
            data-testid="send-button"
          >
            {isLoading ? (
              <Loader2 className="w-5 h-5 animate-spin" />
            ) : (
              <Send className="w-5 h-5" />
            )}
          </button>
        )}
      </div>

      <p className="mt-1 text-xs text-muted-foreground">
        Enter to send · Shift+Enter for new line · / for commands · @ to mention
      </p>
    </div>
  );
}
