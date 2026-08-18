/**
 * ChatSidebar — left panel with session list, folders, pin, and new chat.
 */

import { useState, type JSX } from 'react';
import { Plus, Pin, Folder, Trash2, Search, MessageSquare } from 'lucide-react';
import type { ChatSession, ChatFolder } from './types/chat.types';

interface Props {
  sessions: ChatSession[];
  folders: ChatFolder[];
  activeSessionId: string | undefined;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onDeleteSession: (id: string) => void;
  onPinSession: (id: string, pinned: boolean) => void;
  isLoading?: boolean;
}

export function ChatSidebar({
  sessions,
  folders,
  activeSessionId,
  onSelectSession,
  onNewSession,
  onDeleteSession,
  onPinSession,
  isLoading,
}: Props): JSX.Element {
  const [search, setSearch] = useState('');

  const filtered = sessions.filter((s) =>
    s.title.toLowerCase().includes(search.toLowerCase()),
  );

  const pinned = filtered.filter((s) => s.pinned);
  const unpinned = filtered.filter((s) => !s.pinned);

  function SessionItem({ session }: { session: ChatSession }) {
    const isActive = session.id === activeSessionId;
    return (
      <div
        className={[
          'group flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors',
          isActive
            ? 'bg-indigo-50 dark:bg-indigo-950 text-indigo-700 dark:text-indigo-300'
            : 'hover:bg-gray-100 dark:hover:bg-gray-800 text-[#A0B4CC] dark:text-gray-300',
        ].join(' ')}
        onClick={() => onSelectSession(session.id)}
        role="button"
        tabIndex={0}
        aria-current={isActive ? 'page' : undefined}
        onKeyDown={(e) => e.key === 'Enter' && onSelectSession(session.id)}
        data-testid={`session-${session.id}`}
      >
        <MessageSquare className="w-4 h-4 shrink-0 opacity-60" />
        <span className="flex-1 text-sm truncate">{session.title}</span>

        <div className="hidden group-hover:flex items-center gap-1">
          <button
            className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700"
            onClick={(e) => {
              e.stopPropagation();
              onPinSession(session.id, !session.pinned);
            }}
            aria-label={session.pinned ? 'Unpin session' : 'Pin session'}
          >
            <Pin
              className={`w-3 h-3 ${session.pinned ? 'text-indigo-500' : 'text-[#A0B4CC]'}`}
            />
          </button>
          <button
            className="p-1 rounded hover:bg-red-100 dark:hover:bg-red-950"
            onClick={(e) => {
              e.stopPropagation();
              onDeleteSession(session.id);
            }}
            aria-label="Delete session"
          >
            <Trash2 className="w-3 h-3 text-red-500" />
          </button>
        </div>
      </div>
    );
  }

  return (
    <aside
      className="w-64 shrink-0 flex flex-col border-r border-white/[0.08] dark:border-gray-700 bg-[#0A0F1A] dark:bg-gray-900 h-full"
      aria-label="Chat sessions"
    >
      {/* Header */}
      <div className="p-3 border-b border-white/[0.08] dark:border-gray-700">
        <button
          className="w-full flex items-center gap-2 justify-center py-2 px-4 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-sm font-medium transition-colors"
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
        <div className="flex items-center gap-2 bg-[#0F1826] dark:bg-gray-800 border border-white/[0.08] dark:border-gray-700 rounded-lg px-3 py-1.5">
          <Search className="w-3.5 h-3.5 text-[#A0B4CC] shrink-0" />
          <input
            className="flex-1 text-sm bg-transparent outline-none text-[#A0B4CC] dark:text-gray-300 placeholder-gray-400"
            placeholder="Search chats…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search chat sessions"
          />
        </div>
      </div>

      {/* Sessions list */}
      <nav className="flex-1 overflow-y-auto px-2 py-1 space-y-0.5">
        {isLoading && (
          <p className="text-xs text-[#A0B4CC] px-3 py-2">Loading…</p>
        )}

        {pinned.length > 0 && (
          <div>
            <p className="text-xs font-medium text-[#A0B4CC] px-3 py-1 uppercase tracking-wide">
              Pinned
            </p>
            {pinned.map((s) => (
              <SessionItem key={s.id} session={s} />
            ))}
          </div>
        )}

        {folders.length > 0 &&
          folders.map((folder) => {
            const folderSessions = unpinned.filter((s) => s.folder_id === folder.id);
            if (folderSessions.length === 0) return null;
            return (
              <div key={folder.id}>
                <p className="flex items-center gap-1 text-xs font-medium text-[#A0B4CC] px-3 py-1 uppercase tracking-wide">
                  <Folder
                    className="w-3 h-3"
                    style={{ color: folder.color }}
                  />
                  {folder.name}
                </p>
                {folderSessions.map((s) => (
                  <SessionItem key={s.id} session={s} />
                ))}
              </div>
            );
          })}

        {unpinned.filter((s) => !s.folder_id).length > 0 && (
          <div>
            {(pinned.length > 0 || folders.length > 0) && (
              <p className="text-xs font-medium text-[#A0B4CC] px-3 py-1 uppercase tracking-wide">
                Recent
              </p>
            )}
            {unpinned
              .filter((s) => !s.folder_id)
              .map((s) => (
                <SessionItem key={s.id} session={s} />
              ))}
          </div>
        )}

        {filtered.length === 0 && !isLoading && (
          <p className="text-xs text-[#A0B4CC] px-3 py-4 text-center">
            {search ? 'No matching sessions' : 'No sessions yet. Start a new chat!'}
          </p>
        )}
      </nav>
    </aside>
  );
}
