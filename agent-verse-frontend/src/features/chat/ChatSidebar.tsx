/**
 * ChatSidebar — left panel with session list, folders, pin, and new chat.
 *
 * Folders (CHAT-D-1) are the caller's own and durable on the server: create,
 * rename and delete them here, and file a chat into one with "Move to folder".
 */

import { useState, type JSX } from 'react';
import {
  Plus,
  Pin,
  Folder,
  FolderPlus,
  Trash2,
  Search,
  MessageSquare,
  Pencil,
} from 'lucide-react';
import type { ChatSession, ChatFolder } from './types/chat.types';

interface Props {
  sessions: ChatSession[];
  folders: ChatFolder[];
  activeSessionId: string | undefined;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onDeleteSession: (id: string) => void;
  onPinSession: (id: string, pinned: boolean) => void;
  onRenameSession?: (id: string, title: string) => void;
  onCreateFolder?: (name: string) => void;
  onRenameFolder?: (folderId: string, name: string) => void;
  onDeleteFolder?: (folderId: string) => void;
  /** File a chat into a folder (null: take it out of its folder). */
  onMoveSession?: (sessionId: string, folderId: string | null) => void;
  isLoading?: boolean;
}

/** Inline name editor: Enter commits a changed non-empty name, Escape cancels. */
function NameInput({
  initial,
  label,
  onCommit,
  onCancel,
}: {
  initial: string;
  label: string;
  onCommit: (name: string) => void;
  onCancel: () => void;
}): JSX.Element {
  const [draft, setDraft] = useState(initial);
  const commit = () => {
    const name = draft.trim();
    if (name && name !== initial) onCommit(name);
    else onCancel();
  };
  return (
    <input
      className="flex-1 min-w-0 text-sm bg-transparent border-b border-indigo-500 outline-none text-foreground"
      value={draft}
      autoFocus
      maxLength={200}
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === 'Enter') commit();
        else if (e.key === 'Escape') onCancel();
      }}
      aria-label={label}
    />
  );
}

export function ChatSidebar({
  sessions,
  folders,
  activeSessionId,
  onSelectSession,
  onNewSession,
  onDeleteSession,
  onPinSession,
  onRenameSession,
  onCreateFolder,
  onRenameFolder,
  onDeleteFolder,
  onMoveSession,
  isLoading,
}: Props): JSX.Element {
  const [search, setSearch] = useState('');
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [renamingFolder, setRenamingFolder] = useState<string | null>(null);
  const [confirmingFolderDelete, setConfirmingFolderDelete] = useState<string | null>(null);

  // NOTE(scale): this search runs over the sessions already loaded into the
  // sidebar (capped by chatApi.listSessions' client-side limit — GET
  // /chat/sessions has no server-side limit/search param). It will not match
  // sessions beyond that loaded page. Needs a backend session search + cursor
  // to search the full history server-side.
  const filtered = sessions.filter((s) =>
    s.title.toLowerCase().includes(search.toLowerCase()),
  );

  const pinned = filtered.filter((s) => s.pinned);
  const unpinned = filtered.filter((s) => !s.pinned);
  // A chat filed into a folder this list does not know (deleted elsewhere, or
  // never persisted) is shown as unfiled rather than disappearing.
  const knownFolders = new Set(folders.map((f) => f.id));
  const isUnfiled = (s: ChatSession) => !s.folder_id || !knownFolders.has(s.folder_id);

  function SessionItem({ session }: { session: ChatSession }) {
    const isActive = session.id === activeSessionId;
    const [confirmingDelete, setConfirmingDelete] = useState(false);
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState(session.title);
    const commitRename = () => {
      const title = draft.trim();
      setEditing(false);
      if (title && title !== session.title) onRenameSession?.(session.id, title);
      else setDraft(session.title);
    };
    return (
      <div
        className={[
          'group flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors',
          isActive
            ? 'bg-indigo-50 dark:bg-indigo-950 text-indigo-700 dark:text-indigo-300'
            : 'hover:bg-muted text-muted-foreground',
        ].join(' ')}
        onClick={() => onSelectSession(session.id)}
        role="button"
        tabIndex={0}
        aria-current={isActive ? 'page' : undefined}
        onKeyDown={(e) => e.key === 'Enter' && onSelectSession(session.id)}
        data-testid={`session-${session.id}`}
      >
        <MessageSquare className="w-4 h-4 shrink-0 opacity-60" />
        {editing ? (
          <input
            className="flex-1 min-w-0 text-sm bg-transparent border-b border-indigo-500 outline-none text-foreground"
            value={draft}
            autoFocus
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commitRename}
            onKeyDown={(e) => {
              e.stopPropagation();
              if (e.key === 'Enter') commitRename();
              else if (e.key === 'Escape') {
                setDraft(session.title);
                setEditing(false);
              }
            }}
            aria-label="Rename session"
          />
        ) : (
          <span className="flex-1 text-sm truncate">{session.title}</span>
        )}

        <div className="hidden group-hover:flex items-center gap-1">
          {onMoveSession && folders.length > 0 && (
            <select
              className="max-w-[5.5rem] text-xs bg-card border border-border rounded px-1 py-0.5 text-muted-foreground"
              value={session.folder_id && knownFolders.has(session.folder_id) ? session.folder_id : ''}
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => e.stopPropagation()}
              onChange={(e) => {
                e.stopPropagation();
                onMoveSession(session.id, e.target.value || null);
              }}
              aria-label="Move to folder"
              title="Move to folder"
            >
              <option value="">No folder</option>
              {folders.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                </option>
              ))}
            </select>
          )}
          {onRenameSession && !editing && (
            <button
              className="p-1 rounded hover:bg-muted"
              onClick={(e) => {
                e.stopPropagation();
                setDraft(session.title);
                setEditing(true);
              }}
              aria-label="Rename session"
            >
              <Pencil className="w-3 h-3 text-muted-foreground" />
            </button>
          )}
          <button
            className="p-1 rounded hover:bg-muted"
            onClick={(e) => {
              e.stopPropagation();
              onPinSession(session.id, !session.pinned);
            }}
            aria-label={session.pinned ? 'Unpin session' : 'Pin session'}
          >
            <Pin
              className={`w-3 h-3 ${session.pinned ? 'text-indigo-500' : 'text-muted-foreground'}`}
            />
          </button>
          <button
            className={[
              'p-1 rounded',
              confirmingDelete
                ? 'bg-red-500/20 ring-1 ring-red-500'
                : 'hover:bg-red-100 dark:hover:bg-red-950',
            ].join(' ')}
            onClick={(e) => {
              e.stopPropagation();
              // Two-click confirm — first click arms, second click deletes.
              if (confirmingDelete) {
                onDeleteSession(session.id);
                setConfirmingDelete(false);
              } else {
                setConfirmingDelete(true);
              }
            }}
            onBlur={() => setConfirmingDelete(false)}
            aria-label={confirmingDelete ? 'Confirm delete session' : 'Delete session'}
            title={confirmingDelete ? 'Click again to confirm' : 'Delete'}
          >
            <Trash2
              className={`w-3 h-3 ${confirmingDelete ? 'text-red-600' : 'text-red-500'}`}
            />
          </button>
        </div>
      </div>
    );
  }

  return (
    <aside
      className="w-64 shrink-0 flex flex-col border-r border-border bg-background h-full"
      aria-label="Chat sessions"
    >
      {/* Header */}
      <div className="p-3 border-b border-border">
        <button
          className="w-full flex items-center gap-2 justify-center py-2 px-4 bg-indigo-600 hover:bg-indigo-700 text-foreground rounded-lg text-sm font-medium transition-colors"
          onClick={onNewSession}
          aria-label="New chat session"
          data-testid="new-chat-button"
        >
          <Plus className="w-4 h-4" />
          New Chat
        </button>
      </div>

      {/* Search */}
      <div className="px-3 py-2">
        <div className="flex items-center gap-2 bg-card border border-border rounded-lg px-3 py-1.5">
          <Search className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
          <input
            className="flex-1 text-sm bg-transparent outline-none text-muted-foreground placeholder-gray-400"
            placeholder="Search chats…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search chat sessions"
          />
        </div>
      </div>

      {onCreateFolder && (
        <div className="px-3 pb-1">
          {creatingFolder ? (
            <div className="flex items-center gap-2 px-2 py-1">
              <FolderPlus className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
              <NameInput
                initial=""
                label="New folder name"
                onCommit={(name) => {
                  setCreatingFolder(false);
                  onCreateFolder(name);
                }}
                onCancel={() => setCreatingFolder(false)}
              />
            </div>
          ) : (
            <button
              className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground px-2 py-1 rounded hover:bg-muted"
              onClick={() => setCreatingFolder(true)}
              aria-label="New folder"
            >
              <FolderPlus className="w-3.5 h-3.5" />
              New folder
            </button>
          )}
        </div>
      )}

      {/* Sessions list */}
      <nav className="flex-1 overflow-y-auto px-2 py-1 space-y-0.5">
        {isLoading && (
          <p className="text-xs text-muted-foreground px-3 py-2">Loading…</p>
        )}

        {pinned.length > 0 && (
          <div>
            <p className="text-xs font-medium text-muted-foreground px-3 py-1 uppercase tracking-wide">
              Pinned
            </p>
            {pinned.map((s) => (
              <SessionItem key={s.id} session={s} />
            ))}
          </div>
        )}

        {folders.map((folder) => {
          const folderSessions = unpinned.filter((s) => s.folder_id === folder.id);
          // While searching, a folder with no match stays out of the way.
          if (search && folderSessions.length === 0) return null;
          const confirming = confirmingFolderDelete === folder.id;
          return (
            <div key={folder.id} data-testid={`folder-${folder.id}`}>
              <div className="group flex items-center gap-1 px-3 py-1">
                <Folder className="w-3 h-3 shrink-0" style={{ color: folder.color }} />
                {renamingFolder === folder.id && onRenameFolder ? (
                  <NameInput
                    initial={folder.name}
                    label="Folder name"
                    onCommit={(name) => {
                      setRenamingFolder(null);
                      onRenameFolder(folder.id, name);
                    }}
                    onCancel={() => setRenamingFolder(null)}
                  />
                ) : (
                  <p className="flex-1 truncate text-xs font-medium text-muted-foreground uppercase tracking-wide">
                    {folder.name}
                  </p>
                )}
                <div className="hidden group-hover:flex items-center gap-1">
                  {onRenameFolder && renamingFolder !== folder.id && (
                    <button
                      className="p-1 rounded hover:bg-muted"
                      onClick={() => setRenamingFolder(folder.id)}
                      aria-label={`Rename folder ${folder.name}`}
                    >
                      <Pencil className="w-3 h-3 text-muted-foreground" />
                    </button>
                  )}
                  {onDeleteFolder && (
                    <button
                      className={[
                        'p-1 rounded',
                        confirming
                          ? 'bg-red-500/20 ring-1 ring-red-500'
                          : 'hover:bg-red-100 dark:hover:bg-red-950',
                      ].join(' ')}
                      onClick={() => {
                        // Two-click confirm; the folder's chats are kept, unfiled.
                        if (confirming) {
                          setConfirmingFolderDelete(null);
                          onDeleteFolder(folder.id);
                        } else {
                          setConfirmingFolderDelete(folder.id);
                        }
                      }}
                      onBlur={() => setConfirmingFolderDelete(null)}
                      aria-label={
                        confirming
                          ? `Confirm delete folder ${folder.name}`
                          : `Delete folder ${folder.name}`
                      }
                      title={confirming ? 'Click again to delete (its chats are kept)' : 'Delete folder'}
                    >
                      <Trash2 className="w-3 h-3 text-red-500" />
                    </button>
                  )}
                </div>
              </div>
              {folderSessions.map((s) => (
                <SessionItem key={s.id} session={s} />
              ))}
            </div>
          );
        })}

        {unpinned.filter(isUnfiled).length > 0 && (
          <div>
            {(pinned.length > 0 || folders.length > 0) && (
              <p className="text-xs font-medium text-muted-foreground px-3 py-1 uppercase tracking-wide">
                Recent
              </p>
            )}
            {unpinned.filter(isUnfiled).map((s) => (
              <SessionItem key={s.id} session={s} />
            ))}
          </div>
        )}

        {filtered.length === 0 && !isLoading && (
          <p className="text-xs text-muted-foreground px-3 py-4 text-center">
            {search ? 'No matching sessions' : 'No sessions yet. Start a new chat!'}
          </p>
        )}
      </nav>
    </aside>
  );
}
