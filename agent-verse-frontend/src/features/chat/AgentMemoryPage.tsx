/**
 * AgentMemoryPage — memory management list with edit/delete actions.
 */

import { useCallback, useEffect, useState, type JSX } from 'react';
import { Brain, Trash2, Edit2, Plus, Check, X } from 'lucide-react';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';

interface Memory {
  id: string;
  content: string;
  source: string;
  created_at: string;
}

export default function AgentMemoryPage(): JSX.Element {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState('');
  const [newContent, setNewContent] = useState('');
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch('/chat/memories', {
        headers: { 'X-API-Key': sessionStorage.getItem('agentverse_api_key') ?? '' },
      });
      if (r.ok) {
        const data = await r.json();
        setMemories(data.memories ?? []);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleDelete = async (id: string) => {
    await fetch(`/chat/memories/${id}`, {
      method: 'DELETE',
      headers: { 'X-API-Key': sessionStorage.getItem('agentverse_api_key') ?? '' },
    });
    setMemories((prev) => prev.filter((m) => m.id !== id));
  };

  const handleEdit = async (id: string) => {
    const r = await fetch(`/chat/memories/${id}`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': sessionStorage.getItem('agentverse_api_key') ?? '',
      },
      body: JSON.stringify({ content: editContent }),
    });
    if (r.ok) {
      const data = await r.json();
      setMemories((prev) => prev.map((m) => (m.id === id ? data : m)));
      setEditingId(null);
    }
  };

  const handleAdd = async () => {
    if (!newContent.trim()) return;
    const r = await fetch('/chat/memories', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': sessionStorage.getItem('agentverse_api_key') ?? '',
      },
      body: JSON.stringify({ content: newContent }),
    });
    if (r.ok) {
      const data = await r.json();
      setMemories((prev) => [data, ...prev]);
      setNewContent('');
      setAdding(false);
    }
  };

  return (
    <JARVISPageShell>
    <JARVISStagger className="max-w-2xl mx-auto px-4 py-8">
      <div className="flex items-center gap-3 mb-6">
        <Brain className="w-6 h-6 text-indigo-600" />
        <h1 className="text-xl font-semibold text-gray-800 dark:text-gray-200">Agent Memory</h1>
      </div>
      <p className="text-sm text-gray-500 mb-6">
        Memories help the agent personalise its responses across sessions.
      </p>

      {/* Add new */}
      {adding ? (
        <div className="flex gap-2 mb-4">
          <input
            className="flex-1 text-sm border border-gray-200 dark:border-gray-700 rounded-xl px-3 py-2 bg-gray-50 dark:bg-gray-800 text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            placeholder="New memory…"
            value={newContent}
            onChange={(e) => setNewContent(e.target.value)}
            autoFocus
            aria-label="New memory content"
          />
          <button className="p-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl" onClick={handleAdd} aria-label="Save memory">
            <Check className="w-4 h-4" />
          </button>
          <button className="p-2 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-xl" onClick={() => setAdding(false)} aria-label="Cancel">
            <X className="w-4 h-4 text-gray-400" />
          </button>
        </div>
      ) : (
        <button
          className="flex items-center gap-2 text-sm text-indigo-600 hover:text-indigo-700 mb-4"
          onClick={() => setAdding(true)}
        >
          <Plus className="w-4 h-4" />
          Add memory
        </button>
      )}

      {loading && <p className="text-sm text-gray-400">Loading…</p>}

      <div className="space-y-2" role="list" aria-label="Agent memories">
        {memories.map((m) => (
          <div
            key={m.id}
            className="flex items-start gap-3 p-3 bg-white dark:bg-gray-800 border border-gray-100 dark:border-gray-700 rounded-xl group"
            role="listitem"
          >
            <Brain className="w-4 h-4 text-indigo-400 mt-0.5 shrink-0" />
            {editingId === m.id ? (
              <div className="flex-1 flex gap-2">
                <input
                  className="flex-1 text-sm border border-indigo-300 rounded-lg px-2 py-1 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  value={editContent}
                  onChange={(e) => setEditContent(e.target.value)}
                  autoFocus
                />
                <button onClick={() => handleEdit(m.id)} aria-label="Save edit"><Check className="w-4 h-4 text-green-500" /></button>
                <button onClick={() => setEditingId(null)} aria-label="Cancel edit"><X className="w-4 h-4 text-gray-400" /></button>
              </div>
            ) : (
              <div className="flex-1">
                <p className="text-sm text-gray-700 dark:text-gray-200">{m.content}</p>
                <p className="text-xs text-gray-400 mt-0.5">
                  {m.source} · {new Date(m.created_at).toLocaleDateString()}
                </p>
              </div>
            )}
            <div className="hidden group-hover:flex items-center gap-1">
              <button
                className="p-1 hover:bg-gray-100 dark:hover:bg-gray-700 rounded"
                onClick={() => { setEditingId(m.id); setEditContent(m.content); }}
                aria-label="Edit memory"
              >
                <Edit2 className="w-3 h-3 text-gray-400" />
              </button>
              <button
                className="p-1 hover:bg-red-50 dark:hover:bg-red-950 rounded"
                onClick={() => handleDelete(m.id)}
                aria-label="Delete memory"
              >
                <Trash2 className="w-3 h-3 text-red-400" />
              </button>
            </div>
          </div>
        ))}

        {!loading && memories.length === 0 && (
          <p className="text-sm text-gray-400 text-center py-8">
            No memories yet. The agent will build memories as you chat.
          </p>
        )}
      </div>
    </JARVISStagger>
    </JARVISPageShell>
  );
}
