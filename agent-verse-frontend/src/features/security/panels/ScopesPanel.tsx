import { useState } from 'react';

const BASE_ROLES = [
  { name: 'viewer', scopes: ['goals:read', 'agents:read', 'templates:read'], color: 'blue' },
  { name: 'operator', scopes: ['goals:write', 'agents:run', 'tools:use'], color: 'green' },
  { name: 'builder', scopes: ['agents:write', 'templates:write', 'skills:write'], color: 'purple' },
  { name: 'admin', scopes: ['agents:admin', 'policies:write', 'audit:read'], color: 'orange' },
  { name: 'owner', scopes: ['*'], color: 'red' },
];

const ROLE_COLORS: Record<string, string> = {
  blue: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  green: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  purple: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400',
  orange: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
  red: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
};

export function ScopesPanel() {
  const [showCreateRole, setShowCreateRole] = useState(false);
  const [form, setForm] = useState({ name: '', inherits: 'viewer', extra_scopes: '', denied_scopes: '' });

  return (
    <div className="space-y-6">
      {/* Built-in roles */}
      <div className="rounded-xl border bg-card p-5">
        <h2 className="font-semibold text-foreground mb-4">Built-in Role Hierarchy</h2>
        <div className="space-y-2">
          {BASE_ROLES.map(role => (
            <div key={role.name} className="flex items-center justify-between p-3 rounded-lg border">
              <div className="flex items-center gap-3">
                <span className={`px-2 py-0.5 rounded text-xs font-mono font-medium ${ROLE_COLORS[role.color]}`}>
                  {role.name}
                </span>
                <div className="flex flex-wrap gap-1">
                  {role.scopes.map(s => (
                    <span key={s} className="text-xs px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{s}</span>
                  ))}
                </div>
              </div>
            </div>
          ))}
        </div>
        <p className="text-xs text-muted-foreground mt-3">
          Roles inherit from lower tiers — owner gets all scopes, viewer gets read-only. Custom roles extend any base role.
        </p>
      </div>

      {/* Custom role creator */}
      <div className="rounded-xl border bg-card p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-semibold text-foreground">Custom Roles</h2>
          <button
            onClick={() => setShowCreateRole(!showCreateRole)}
            className="px-3 py-1.5 rounded bg-primary text-primary-foreground text-xs font-medium"
          >
            + Create Role
          </button>
        </div>

        {showCreateRole && (
          <div className="rounded-lg border bg-muted/30 p-4 space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Role Name</label>
                <input className="w-full border rounded px-3 py-1.5 text-sm bg-background text-foreground"
                  placeholder="data-entry" value={form.name}
                  onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Inherits From</label>
                <select className="w-full border rounded px-3 py-1.5 text-sm bg-background text-foreground"
                  value={form.inherits} onChange={e => setForm(f => ({ ...f, inherits: e.target.value }))}>
                  <option value="viewer">viewer</option>
                  <option value="operator">operator</option>
                  <option value="builder">builder</option>
                  <option value="admin">admin</option>
                </select>
              </div>
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Extra Scopes (comma-separated)</label>
              <input className="w-full border rounded px-3 py-1.5 text-sm bg-background text-foreground"
                placeholder="goals:write, templates:read" value={form.extra_scopes}
                onChange={e => setForm(f => ({ ...f, extra_scopes: e.target.value }))} />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Denied Scopes (comma-separated)</label>
              <input className="w-full border rounded px-3 py-1.5 text-sm bg-background text-foreground"
                placeholder="agents:admin, policies:write" value={form.denied_scopes}
                onChange={e => setForm(f => ({ ...f, denied_scopes: e.target.value }))} />
            </div>
            <button className="px-4 py-2 rounded bg-primary text-primary-foreground text-sm">Create Role</button>
          </div>
        )}
      </div>
    </div>
  );
}
