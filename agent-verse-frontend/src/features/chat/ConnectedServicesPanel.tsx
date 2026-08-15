/**
 * ConnectedServicesPanel — lists MCP connectors with connect/disconnect actions.
 */

import { useEffect, useState, type JSX } from 'react';
import { Plug, PlugZap, Trash2, Plus } from 'lucide-react';

interface Service {
  id: string;
  name: string;
  url: string;
  scopes: string[];
  status: string;
  connected_at: string;
}

interface Props {
  onClose?: () => void;
}

const API_KEY = () => sessionStorage.getItem('agentverse_api_key') ?? '';
const H = () => ({ 'Content-Type': 'application/json', 'X-API-Key': API_KEY() });

export function ConnectedServicesPanel({ onClose }: Props): JSX.Element {
  const [services, setServices] = useState<Service[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [newName, setNewName] = useState('');
  const [newUrl, setNewUrl] = useState('');

  useEffect(() => {
    fetch('/chat/services', { headers: H() })
      .then((r) => r.json())
      .then((d) => setServices(d.services ?? []))
      .finally(() => setLoading(false));
  }, []);

  const disconnect = async (id: string) => {
    await fetch(`/chat/services/${id}`, { method: 'DELETE', headers: H() });
    setServices((prev) => prev.filter((s) => s.id !== id));
  };

  const connect = async () => {
    if (!newName || !newUrl) return;
    const r = await fetch('/chat/services', {
      method: 'POST',
      headers: H(),
      body: JSON.stringify({ name: newName, url: newUrl }),
    });
    if (r.ok) {
      const data = await r.json();
      setServices((prev) => [...prev, { id: data.service_id, name: newName, url: newUrl, scopes: [], status: 'connected', connected_at: new Date().toISOString() }]);
      setShowAdd(false);
      setNewName('');
      setNewUrl('');
    }
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
        <div className="flex items-center gap-2">
          <PlugZap className="w-4 h-4 text-indigo-500" />
          <h2 className="text-sm font-semibold text-gray-800 dark:text-gray-200">Connected Services</h2>
        </div>
        {onClose && (
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xs" aria-label="Close panel">✕</button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-2">
        {loading && <p className="text-xs text-gray-400">Loading…</p>}

        {services.map((s) => (
          <div
            key={s.id}
            className="flex items-center gap-3 p-3 bg-gray-50 dark:bg-gray-800 border border-gray-100 dark:border-gray-700 rounded-xl group"
          >
            <Plug className="w-4 h-4 text-green-500 shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-gray-700 dark:text-gray-200 truncate">{s.name}</p>
              <p className="text-xs text-gray-400 truncate">{s.url}</p>
            </div>
            <span className="text-xs text-green-500 bg-green-50 dark:bg-green-950 px-1.5 py-0.5 rounded-full shrink-0">
              {s.status}
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
          <p className="text-xs text-gray-400 text-center py-6">
            No services connected. Add an MCP tool to extend agent capabilities.
          </p>
        )}
      </div>

      {/* Add service */}
      <div className="p-4 border-t border-gray-200 dark:border-gray-700">
        {showAdd ? (
          <div className="space-y-2">
            <input
              className="w-full text-xs border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              placeholder="Service name"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
            />
            <input
              className="w-full text-xs border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              placeholder="MCP server URL"
              value={newUrl}
              onChange={(e) => setNewUrl(e.target.value)}
            />
            <div className="flex gap-2">
              <button className="flex-1 py-1.5 bg-indigo-600 text-white text-xs rounded-lg hover:bg-indigo-700" onClick={connect}>
                Connect
              </button>
              <button className="flex-1 py-1.5 border border-gray-200 text-xs rounded-lg text-gray-500 hover:bg-gray-50" onClick={() => setShowAdd(false)}>
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
