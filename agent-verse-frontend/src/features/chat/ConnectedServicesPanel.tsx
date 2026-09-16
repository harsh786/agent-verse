/**
 * ConnectedServicesPanel — lists MCP connectors with connect/disconnect actions.
 */

import { useEffect, useState, type JSX } from 'react';
import { Plug, PlugZap, Trash2, Plus } from 'lucide-react';
import { getAuthHeader } from '@/stores/auth';
import { API_BASE } from '@/lib/api/client';

interface Service {
  id: string;
  name: string;
  url: string;
  scopes: string[];
  status: string;
  connected_at: string | null;
}

// Status → badge colour. 'pending' means OAuth hasn't completed yet, so it must
// NOT read as a healthy green "connected".
const STATUS_STYLE: Record<string, string> = {
  connected: 'text-green-500 bg-green-50 dark:bg-green-950',
  pending: 'text-amber-600 bg-amber-50 dark:bg-amber-950',
  error: 'text-red-500 bg-red-50 dark:bg-red-950',
  disconnected: 'text-muted-foreground bg-muted',
};
const statusStyle = (s: string) => STATUS_STYLE[s] ?? STATUS_STYLE.disconnected;

interface Props {
  onClose?: () => void;
}

const H = () => ({ 'Content-Type': 'application/json', ...getAuthHeader() });

export function ConnectedServicesPanel({ onClose }: Props): JSX.Element {
  const [services, setServices] = useState<Service[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [newName, setNewName] = useState('');
  const [newUrl, setNewUrl] = useState('');

  useEffect(() => {
    fetch(`${API_BASE}/chat/services`, { headers: H() })
      .then((r) => r.json())
      .then((d) => setServices(d.services ?? []))
      .finally(() => setLoading(false));
  }, []);

  const disconnect = async (id: string) => {
    await fetch(`${API_BASE}/chat/services/${id}`, { method: 'DELETE', headers: H() });
    setServices((prev) => prev.filter((s) => s.id !== id));
  };

  const connect = async () => {
    if (!newName || !newUrl) return;
    const r = await fetch(`${API_BASE}/chat/services`, {
      method: 'POST',
      headers: H(),
      body: JSON.stringify({ name: newName, url: newUrl }),
    });
    if (r.ok) {
      const data = await r.json();
      // The backend returns status "pending" — OAuth is not complete yet, so do
      // not claim "connected". The user finishes auth via data.oauth_url.
      setServices((prev) => [...prev, { id: data.service_id, name: newName, url: newUrl, scopes: [], status: data.status ?? 'pending', connected_at: null }]);
      setShowAdd(false);
      setNewName('');
      setNewUrl('');
    }
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <PlugZap className="w-4 h-4 text-indigo-500" />
          <h2 className="text-sm font-semibold text-foreground">Connected Services</h2>
        </div>
        {onClose && (
          <button onClick={onClose} className="text-muted-foreground hover:text-muted-foreground text-xs" aria-label="Close panel">✕</button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-2">
        {loading && <p className="text-xs text-muted-foreground">Loading…</p>}

        {services.map((s) => (
          <div
            key={s.id}
            className="flex items-center gap-3 p-3 bg-background border border-border rounded-xl group"
          >
            <Plug className={`w-4 h-4 shrink-0 ${s.status === 'connected' ? 'text-green-500' : s.status === 'pending' ? 'text-amber-500' : 'text-muted-foreground'}`} />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-muted-foreground truncate">{s.name}</p>
              <p className="text-xs text-muted-foreground truncate">{s.url}</p>
            </div>
            <span className={`text-xs px-1.5 py-0.5 rounded-full shrink-0 ${statusStyle(s.status)}`}>
              {s.status === 'pending' ? 'authorizing…' : s.status}
            </span>
            <button
              className="hidden group-hover:block p-1 hover:bg-red-50 dark:hover:bg-red-950 rounded"
              onClick={() => disconnect(s.id)}
              aria-label={`Disconnect ${s.name}`}
            >
              <Trash2 className="w-3 h-3 text-red-400" />
            </button>
          </div>
        ))}

        {!loading && services.length === 0 && (
          <p className="text-xs text-muted-foreground text-center py-6">
            No services connected. Add an MCP tool to extend agent capabilities.
          </p>
        )}
      </div>

      {/* Add service */}
      <div className="p-4 border-t border-border">
        {showAdd ? (
          <div className="space-y-2">
            <input
              className="w-full text-xs border border-border rounded-lg px-3 py-2 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              placeholder="Service name"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
            />
            <input
              className="w-full text-xs border border-border rounded-lg px-3 py-2 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              placeholder="MCP server URL"
              value={newUrl}
              onChange={(e) => setNewUrl(e.target.value)}
            />
            <div className="flex gap-2">
              <button className="flex-1 py-1.5 bg-indigo-600 text-white text-xs rounded-lg hover:bg-indigo-700" onClick={connect}>
                Connect
              </button>
              <button className="flex-1 py-1.5 border border-border text-xs rounded-lg text-muted-foreground/70 hover:bg-muted" onClick={() => setShowAdd(false)}>
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <button
            className="flex items-center gap-2 text-xs text-indigo-600 hover:text-indigo-700"
            onClick={() => setShowAdd(true)}
          >
            <Plus className="w-3 h-3" />
            Add service
          </button>
        )}
      </div>
    </div>
  );
}
