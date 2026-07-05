/**
 * CRDTEditor — Collaborative text editor backed by Yjs CRDT.
 *
 * Renders a textarea with remote cursor overlays and an awareness strip
 * showing who is editing. Uses useYjsCollab hook for real-time sync.
 */
import { useRef, useEffect, useCallback } from 'react';
import { Wifi, WifiOff, Loader2, Undo2, Redo2 } from 'lucide-react';
import type { CursorInfo } from '@/hooks/useYjsCollab';
import { useYjsCollab } from '@/hooks/useYjsCollab';

interface CRDTEditorProps {
  roomId: string;
  userName?: string;
  userColor?: string;
  placeholder?: string;
  minHeight?: string;
  /** Called with the latest text value on every change */
  onChange?: (text: string) => void;
  /** Initial content (only applied once, before CRDT sync arrives) */
  initialContent?: string;
  className?: string;
  readOnly?: boolean;
}

/** Render a colored dot + name for each remote cursor */
function AwarenessPill({ cursor }: { cursor: CursorInfo }) {
  return (
    <div
      className="flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-medium text-white"
      style={{ backgroundColor: cursor.color }}
    >
      <span className="w-1.5 h-1.5 rounded-full bg-white/60 animate-pulse" />
      {cursor.name}
    </div>
  );
}

export function CRDTEditor({
  roomId,
  userName,
  userColor,
  placeholder = 'Start typing to collaborate…',
  minHeight = '200px',
  onChange,
  initialContent,
  className = '',
  readOnly = false,
}: CRDTEditorProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const initialApplied = useRef(false);

  const { text, setText, cursors, connected, synced, undo, redo, canUndo, canRedo, updateCursorPosition } =
    useYjsCollab({ roomId, userName, color: userColor });

  // Apply initial content once: only when CRDT document is empty after first sync
  useEffect(() => {
    if (synced && !initialApplied.current && initialContent && text === '') {
      setText(initialContent, 'init');
      initialApplied.current = true;
    }
  }, [synced, initialContent, text, setText]);

  // Notify parent on every CRDT-driven change
  useEffect(() => {
    onChange?.(text);
  }, [text, onChange]);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      if (readOnly) return;
      setText(e.target.value, 'local');
    },
    [setText, readOnly],
  );

  // Ctrl+Z / Ctrl+Y shortcuts routed through Yjs UndoManager
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
        e.preventDefault();
        undo();
      } else if (
        (e.ctrlKey || e.metaKey) &&
        (e.key === 'y' || (e.key === 'z' && e.shiftKey))
      ) {
        e.preventDefault();
        redo();
      }
    },
    [undo, redo],
  );

  // Connection status indicator
  const statusNode = connected ? (
    <>
      <Wifi className="h-3.5 w-3.5 text-green-500" />
      <span className="text-green-600 dark:text-green-400">Live</span>
    </>
  ) : synced ? (
    <>
      <WifiOff className="h-3.5 w-3.5 text-amber-500" />
      <span className="text-amber-600 dark:text-amber-400">Offline</span>
    </>
  ) : (
    <>
      <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
      <span className="text-muted-foreground">Connecting…</span>
    </>
  );

  return (
    <div
      className={`flex flex-col border border-input rounded-xl overflow-hidden bg-background ${className}`}
    >
      {/* Toolbar: connection status + undo/redo + remote cursors */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border bg-muted/30">
        {/* Connection indicator */}
        <div className="flex items-center gap-1.5 text-xs mr-2">{statusNode}</div>

        {/* Undo / Redo buttons */}
        {!readOnly && (
          <>
            <button
              onClick={undo}
              disabled={!canUndo}
              className="p-1 rounded hover:bg-muted disabled:opacity-30 transition-colors"
              title="Undo (Ctrl+Z)"
            >
              <Undo2 className="h-3.5 w-3.5" />
            </button>
            <button
              onClick={redo}
              disabled={!canRedo}
              className="p-1 rounded hover:bg-muted disabled:opacity-30 transition-colors"
              title="Redo (Ctrl+Y)"
            >
              <Redo2 className="h-3.5 w-3.5" />
            </button>
            {(canUndo || canRedo) && <div className="w-px h-4 bg-border mx-1" />}
          </>
        )}

        {/* Remote cursor awareness pills */}
        <div className="flex items-center gap-1.5 flex-1 overflow-hidden">
          {cursors.length > 0 ? (
            cursors.map((c) => <AwarenessPill key={c.clientId} cursor={c} />)
          ) : (
            <span className="text-[10px] text-muted-foreground">Only you here</span>
          )}
        </div>

        {/* Character count */}
        <span className="text-[10px] text-muted-foreground ml-auto">{text.length} chars</span>
      </div>

      {/* Editor area */}
      <div className="relative flex-1">
        <textarea
          ref={textareaRef}
          value={text}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          onSelect={(e) => {
            const target = e.target as HTMLTextAreaElement;
            updateCursorPosition(target.selectionStart, {
              anchor: target.selectionStart,
              head: target.selectionEnd,
            });
          }}
          onKeyUp={(e) => {
            const target = e.target as HTMLTextAreaElement;
            updateCursorPosition(target.selectionStart);
          }}
          placeholder={placeholder}
          readOnly={readOnly}
          spellCheck={false}
          className="w-full px-4 py-3 bg-transparent resize-none focus:outline-none font-mono text-sm leading-relaxed"
          style={{ minHeight }}
          aria-label="Collaborative editor"
        />
      </div>

      {/* Offline footer */}
      {!connected && text.length > 0 && (
        <div className="px-3 py-2 border-t border-border bg-muted/30 text-[10px] text-muted-foreground">
          Working offline — changes will sync when reconnected
        </div>
      )}
    </div>
  );
}
