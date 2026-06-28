import { useState } from "react";
import type { JSX } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2, ChevronRight, Plus, Users, Shield } from "lucide-react";
import { rbacApi } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { ConfirmModal } from "@/components/ui/ConfirmModal";
import { toast } from "@/stores/toast";

const ROLES = ["admin", "approver", "operator", "viewer"] as const;
type Role = (typeof ROLES)[number];

// Role hierarchy definition (parent → children)
const ROLE_HIERARCHY: Record<Role, Role[]> = {
  admin: ["approver", "operator", "viewer"],
  approver: ["viewer"],
  operator: ["viewer"],
  viewer: [],
};

const ROLE_SCOPES: Record<Role, string[]> = {
  admin: ["goals:*", "agents:*", "governance:*", "analytics:*"],
  approver: ["goals:read", "governance:approve", "analytics:read"],
  operator: ["goals:write", "goals:cancel", "agents:read"],
  viewer: ["goals:read", "agents:read", "analytics:read"],
};

const ROLE_COLORS: Record<Role, string> = {
  admin: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
  approver: "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400",
  operator: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  viewer: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
};

// ── CIDR validation ───────────────────────────────────────────────────────────

function isValidCidr(cidr: string): boolean {
  const re =
    /^(25[0-5]|2[0-4]\d|[01]?\d\d?)\.(25[0-5]|2[0-4]\d|[01]?\d\d?)\.(25[0-5]|2[0-4]\d|[01]?\d\d?)\.(25[0-5]|2[0-4]\d|[01]?\d\d?)\/(3[0-2]|[12]?\d)$/;
  return re.test(cidr);
}

// ── Role Hierarchy Tree ───────────────────────────────────────────────────────

function RoleNode({ role, depth = 0 }: { role: Role; depth?: number }): JSX.Element {
  const [expanded, setExpanded] = useState(depth === 0);
  const children = ROLE_HIERARCHY[role];

  return (
    <div className={depth > 0 ? "ml-6 border-l border-border pl-4" : ""}>
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-2 py-2 w-full text-left hover:bg-muted/30 rounded-lg px-2 -mx-2 transition-colors"
        aria-expanded={expanded}
      >
        {children.length > 0 ? (
          <ChevronRight
            className={`h-3.5 w-3.5 text-muted-foreground transition-transform ${expanded ? "rotate-90" : ""}`}
          />
        ) : (
          <div className="w-3.5 h-3.5" />
        )}
        <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${ROLE_COLORS[role]}`}>
          {role}
        </span>
        <div className="flex gap-1 flex-wrap">
          {ROLE_SCOPES[role].slice(0, 3).map((s) => (
            <span key={s} className="px-1.5 py-0.5 bg-muted rounded text-xs font-mono">
              {s}
            </span>
          ))}
          {ROLE_SCOPES[role].length > 3 && (
            <span className="text-xs text-muted-foreground">+{ROLE_SCOPES[role].length - 3}</span>
          )}
        </div>
      </button>
      {expanded && children.length > 0 && (
        <div className="mt-1">
          {children.map((child) => (
            <RoleNode key={child} role={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Grant Role Modal ──────────────────────────────────────────────────────────

interface GrantRoleModalProps {
  open: boolean;
  onClose: () => void;
  onGranted: () => void;
}

function GrantRoleModal({ open, onClose, onGranted }: GrantRoleModalProps): JSX.Element | null {
  const qc = useQueryClient();
  const [userId, setUserId] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [condition, setCondition] = useState("");

  const grantMutation = useMutation({
    mutationFn: () =>
      rbacApi.createRole(
        userId,
        condition ? `${role}:${condition}` : role
      ),
    onSuccess: () => {
      toast({ kind: "success", message: "Role assigned" });
      qc.invalidateQueries({ queryKey: ["rbac-roles"] });
      onGranted();
      onClose();
      setUserId("");
      setCondition("");
    },
    onError: (e) => toast({ kind: "error", message: `Failed: assign role. ${String(e)}` }),
  });

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center p-4" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />
      <div className="relative bg-card border border-border rounded-xl shadow-xl max-w-md w-full p-6 space-y-4">
        <h2 className="text-lg font-semibold">Grant Role</h2>

        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1">User ID or email</label>
          <input
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            placeholder="user@example.com"
            className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background"
          />
        </div>

        <div>
          <p className="text-xs font-medium text-muted-foreground mb-2">Role</p>
          <div className="grid grid-cols-2 gap-2">
            {ROLES.map((r) => (
              <button
                key={r}
                onClick={() => setRole(r)}
                className={`px-3 py-2 rounded-lg text-xs border transition-colors ${
                  role === r ? ROLE_COLORS[r] + " border-current" : "border-border hover:bg-muted"
                }`}
              >
                <span className="font-medium capitalize">{r}</span>
                <p className="text-muted-foreground mt-0.5 text-left">{ROLE_SCOPES[r].slice(0, 2).join(", ")}</p>
              </button>
            ))}
          </div>
        </div>

        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1">
            Condition (optional)
          </label>
          <input
            value={condition}
            onChange={(e) => setCondition(e.target.value)}
            placeholder='e.g. agent_id="abc123"'
            className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background font-mono"
          />
          <p className="text-xs text-muted-foreground mt-1">
            Limit this role to specific resources.
          </p>
        </div>

        <div className="flex justify-end gap-3 pt-2">
          <button onClick={onClose} className="px-4 py-2 text-sm border border-border rounded-md hover:bg-muted">
            Cancel
          </button>
          <button
            onClick={() => grantMutation.mutate()}
            disabled={!userId.trim() || grantMutation.isPending}
            className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:opacity-90 disabled:opacity-50"
          >
            {grantMutation.isPending ? "Granting…" : "Grant Role"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Custom Role Creator ───────────────────────────────────────────────────────

const ALL_SCOPES = [
  "goals:read", "goals:write", "goals:cancel", "goals:batch",
  "agents:read", "agents:write", "agents:delete",
  "knowledge:read", "knowledge:write",
  "governance:read", "governance:approve",
  "analytics:read", "analytics:export",
] as const;

function CustomRoleCreator(): JSX.Element {
  const [name, setName] = useState("");
  const [parentRole, setParentRole] = useState<Role>("viewer");
  const [selectedScopes, setSelectedScopes] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const toggleScope = (scope: string): void => {
    setSelectedScopes((prev) =>
      prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope]
    );
  };

  const handleCreate = (): void => {
    if (!name.trim()) return;
    setCreating(true);
    // In production, POST to /tenants/me/custom-roles
    setTimeout(() => {
      toast({ kind: "success", message: `Custom role "${name}" created` });
      setName("");
      setSelectedScopes([]);
      setCreating(false);
      setExpanded(false);
    }, 500);
  };

  if (!expanded) {
    return (
      <button
        onClick={() => setExpanded(true)}
        className="flex items-center gap-2 text-sm text-primary hover:underline"
      >
        <Plus className="h-4 w-4" /> Create custom role
      </button>
    );
  }

  return (
    <div className="bg-muted/30 border border-border rounded-xl p-5 space-y-4">
      <h3 className="text-sm font-semibold">Create Custom Role</h3>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1">Role Name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="data-analyst"
            className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background font-mono"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1">
            Inherits from
          </label>
          <select
            value={parentRole}
            onChange={(e) => setParentRole(e.target.value as Role)}
            className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background"
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>{r}</option>
            ))}
          </select>
        </div>
      </div>
      <div>
        <p className="text-xs font-medium text-muted-foreground mb-2">Additional Scopes</p>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-1.5">
          {ALL_SCOPES.map((scope) => (
            <label key={scope} className="flex items-center gap-1.5 text-xs cursor-pointer">
              <input
                type="checkbox"
                checked={selectedScopes.includes(scope)}
                onChange={() => toggleScope(scope)}
                className="rounded"
              />
              <span className="font-mono">{scope}</span>
            </label>
          ))}
        </div>
      </div>
      <div className="flex gap-2">
        <button
          onClick={handleCreate}
          disabled={!name.trim() || creating}
          className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:opacity-90 disabled:opacity-50"
        >
          {creating ? "Creating…" : "Create Role"}
        </button>
        <button
          onClick={() => setExpanded(false)}
          className="px-4 py-2 text-sm border border-border rounded-md hover:bg-muted"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

// ── RbacPage ──────────────────────────────────────────────────────────────────

export function RbacPage(): JSX.Element {
  const qc = useQueryClient();
  const [cidr, setCidr] = useState("");
  const [cidrDesc, setCidrDesc] = useState("");
  const [cidrError, setCidrError] = useState("");
  const [showGrantModal, setShowGrantModal] = useState(false);
  const [deleteRoleTarget, setDeleteRoleTarget] = useState<string | null>(null);

  const roles = useQuery({ queryKey: ["rbac-roles"], queryFn: () => rbacApi.listRoles() });
  const ips = useQuery({ queryKey: ["rbac-ip"], queryFn: () => rbacApi.listIpAllowlist() });

  const deleteRole = useMutation({
    mutationFn: (id: string) => rbacApi.deleteRole(id),
    onSuccess: () => {
      toast({ kind: "success", message: "Role assignment deleted" });
      qc.invalidateQueries({ queryKey: ["rbac-roles"] });
      setDeleteRoleTarget(null);
    },
    onError: (e) => toast({ kind: "error", message: `Failed: delete role. ${String(e)}` }),
  });

  const addIp = useMutation({
    mutationFn: () => rbacApi.addIpAllowlist(cidr, cidrDesc),
    onSuccess: () => {
      toast({ kind: "success", message: "CIDR added" });
      setCidr("");
      setCidrDesc("");
      setCidrError("");
      qc.invalidateQueries({ queryKey: ["rbac-ip"] });
    },
    onError: (e) => toast({ kind: "error", message: `Failed: add CIDR. ${String(e)}` }),
  });

  const deleteIp = useMutation({
    mutationFn: (id: string) => rbacApi.deleteIpAllowlist(id),
    onSuccess: () => {
      toast({ kind: "success", message: "CIDR deleted" });
      qc.invalidateQueries({ queryKey: ["rbac-ip"] });
    },
    onError: (e) => toast({ kind: "error", message: `Failed: delete CIDR. ${String(e)}` }),
  });

  const handleAddIp = (): void => {
    if (!isValidCidr(cidr)) {
      setCidrError("Invalid CIDR format (e.g. 10.0.0.0/8)");
      return;
    }
    setCidrError("");
    addIp.mutate();
  };

  return (
    <div className="p-4 md:p-6 max-w-4xl mx-auto space-y-8">
      <div>
        <h1 className="text-2xl font-bold">Access Control</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Manage team roles, custom permissions, and network allowlists.
        </p>
      </div>

      {/* Role hierarchy tree */}
      <section className="space-y-3">
        <h2 className="font-semibold flex items-center gap-2">
          <Shield className="h-4 w-4" /> Role Hierarchy
        </h2>
        <div className="bg-card border border-border rounded-xl p-5">
          {ROLES.filter((r) => r === "admin").map((r) => (
            <RoleNode key={r} role={r} />
          ))}
        </div>
      </section>

      {/* Team roles */}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold flex items-center gap-2">
            <Users className="h-4 w-4" /> Team Role Assignments
          </h2>
          <button
            onClick={() => setShowGrantModal(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-lg hover:opacity-90"
          >
            <Plus className="h-3.5 w-3.5" /> Grant Role
          </button>
        </div>

        {roles.isLoading ? (
          <Skeleton className="h-10 w-full" />
        ) : (roles.data ?? []).length === 0 ? (
          <EmptyState title="No role assignments" />
        ) : (
          <div className="space-y-2">
            {(roles.data ?? []).map((r) => {
              const roleKey = ROLES.find((ro) => r.role.startsWith(ro));
              return (
                <div
                  key={r.id}
                  className="flex items-center justify-between bg-card border border-border rounded-lg px-4 py-3 text-sm"
                >
                  <div className="flex items-center gap-3">
                    <span className="font-medium">{r.user_id}</span>
                    {roleKey && (
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${ROLE_COLORS[roleKey]}`}>
                        {r.role}
                      </span>
                    )}
                    {ROLE_SCOPES[roleKey ?? "viewer"]?.slice(0, 2).map((s) => (
                      <span key={s} className="px-1.5 py-0.5 bg-muted rounded text-xs font-mono hidden md:inline">
                        {s}
                      </span>
                    ))}
                  </div>
                  <button
                    onClick={() => setDeleteRoleTarget(r.id)}
                    aria-label={`Remove role ${r.id}`}
                    className="p-1.5 rounded-md hover:bg-accent text-muted-foreground"
                  >
                    <Trash2 className="h-4 w-4" aria-hidden="true" />
                  </button>
                </div>
              );
            })}
          </div>
        )}

        <CustomRoleCreator />
      </section>

      {/* IP allowlist */}
      <section className="space-y-3">
        <h2 className="font-semibold">IP Allowlist</h2>
        <div className="flex flex-wrap gap-3 items-end bg-card border border-border rounded-xl p-4">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            CIDR
            <input
              aria-label="CIDR"
              value={cidr}
              onChange={(e) => { setCidr(e.target.value); setCidrError(""); }}
              placeholder="10.0.0.0/8"
              className={`px-2 py-1.5 border rounded-md bg-background text-sm font-mono ${
                cidrError ? "border-red-400" : "border-input"
              }`}
            />
            {cidrError && <span className="text-red-500 text-xs">{cidrError}</span>}
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground flex-1 min-w-[12rem]">
            Description
            <input
              value={cidrDesc}
              onChange={(e) => setCidrDesc(e.target.value)}
              className="px-2 py-1.5 border border-input rounded-md bg-background text-sm"
            />
          </label>
          <button
            onClick={handleAddIp}
            disabled={!cidr || addIp.isPending}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm hover:opacity-90 disabled:opacity-50"
          >
            Add CIDR
          </button>
        </div>
        {ips.isLoading ? (
          <Skeleton className="h-10 w-full" />
        ) : (ips.data ?? []).length === 0 ? (
          <EmptyState
            title="No allowlist entries"
            description="All IPs are allowed until an entry is added."
          />
        ) : (
          <div className="space-y-2">
            {(ips.data ?? []).map((e) => (
              <div
                key={e.id}
                className="flex items-center justify-between bg-card border border-border rounded-lg px-4 py-2.5 text-sm"
              >
                <span>
                  <span className="font-mono">{e.cidr}</span>
                  {e.description ? ` — ${e.description}` : ""}
                </span>
                <button
                  onClick={() => deleteIp.mutate(e.id)}
                  aria-label={`Remove CIDR ${e.id}`}
                  className="p-1.5 rounded-md hover:bg-accent text-muted-foreground"
                >
                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Modals */}
      <GrantRoleModal
        open={showGrantModal}
        onClose={() => setShowGrantModal(false)}
        onGranted={() => qc.invalidateQueries({ queryKey: ["rbac-roles"] })}
      />

      <ConfirmModal
        open={!!deleteRoleTarget}
        title="Remove role assignment?"
        description="The user will lose this role's permissions immediately."
        confirmLabel="Remove"
        variant="danger"
        isLoading={deleteRole.isPending}
        onConfirm={() => deleteRoleTarget && deleteRole.mutate(deleteRoleTarget)}
        onCancel={() => setDeleteRoleTarget(null)}
      />
    </div>
  );
}

export default RbacPage;
