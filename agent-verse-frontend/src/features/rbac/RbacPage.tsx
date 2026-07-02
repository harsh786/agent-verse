/**
 * RbacPage — Access Control Center
 *
 * Two tabs:
 *   Role Assignments — stats row, search, bulk-delete, table, hierarchy tree, role summary
 *   IP Allowlist     — CIDR form with validation, entry table, network info banner
 */
import { useMemo, useState } from "react";
import type { JSX } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  ChevronRight,
  Key,
  Network,
  Plus,
  Search,
  Shield,
  Trash2,
  Users,
} from "lucide-react";
import { rbacApi } from "@/lib/api/client";
import type { IpAllowlistEntry, RoleAssignment } from "@/lib/api/client";
import { ConfirmModal } from "@/components/ui/ConfirmModal";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { toast } from "@/stores/toast";

// ── Constants ─────────────────────────────────────────────────────────────────

const ROLES = ["admin", "approver", "operator", "viewer"] as const;
type Role = (typeof ROLES)[number];
type RbacTab = "roles" | "ip";

const ROLE_HIERARCHY: Record<Role, Role[]> = {
  admin: ["approver", "operator"],
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

const ROLE_DESCRIPTIONS: Record<Role, string> = {
  admin: "Full tenant access; manages roles and policies",
  approver: "Reviews and approves HITL requests",
  operator: "Creates and cancels goals, reads agents",
  viewer: "Read-only access to goals, agents, and analytics",
};

const ROLE_PILL: Record<Role, string> = {
  admin:
    "bg-red-100 text-red-700 border border-red-200 dark:bg-red-900/30 dark:text-red-400 dark:border-red-800",
  approver:
    "bg-orange-100 text-orange-700 border border-orange-200 dark:bg-orange-900/30 dark:text-orange-400 dark:border-orange-800",
  operator:
    "bg-blue-100 text-blue-700 border border-blue-200 dark:bg-blue-900/30 dark:text-blue-400 dark:border-blue-800",
  viewer:
    "bg-green-100 text-green-700 border border-green-200 dark:bg-green-900/30 dark:text-green-400 dark:border-green-800",
};

const ROLE_ICON_BG: Record<Role, string> = {
  admin: "bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400",
  approver: "bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400",
  operator: "bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400",
  viewer: "bg-green-100 text-green-600 dark:bg-green-900/30 dark:text-green-400",
};

// ── Utilities ─────────────────────────────────────────────────────────────────

function isValidCidr(cidr: string): boolean {
  return /^(25[0-5]|2[0-4]\d|[01]?\d\d?)\.(25[0-5]|2[0-4]\d|[01]?\d\d?)\.(25[0-5]|2[0-4]\d|[01]?\d\d?)\.(25[0-5]|2[0-4]\d|[01]?\d\d?)\/(3[0-2]|[12]?\d)$/.test(
    cidr
  );
}

function fmtDate(iso?: string): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function resolveRoleKey(roleStr: string): Role {
  return ROLES.find((r) => roleStr.startsWith(r)) ?? "viewer";
}

// ── RoleNode — hierarchy tree item ────────────────────────────────────────────

function RoleNode({ role, depth = 0 }: { role: Role; depth?: number }): JSX.Element {
  const [open, setOpen] = useState(depth === 0);
  const children = ROLE_HIERARCHY[role];

  return (
    <div className={depth > 0 ? "ml-5 pl-4 border-l border-border" : ""}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 py-1.5 w-full text-left hover:bg-muted/30 rounded px-1.5 -mx-1.5 transition-colors"
        aria-expanded={open}
      >
        {children.length > 0 ? (
          <ChevronRight
            className={`h-3.5 w-3.5 text-muted-foreground transition-transform duration-150 ${
              open ? "rotate-90" : ""
            }`}
          />
        ) : (
          <span className="w-3.5 h-3.5 inline-block" />
        )}
        <span className={`px-2.5 py-0.5 rounded-full text-xs font-semibold ${ROLE_PILL[role]}`}>
          {role}
        </span>
        <span className="text-xs text-muted-foreground truncate">
          {ROLE_SCOPES[role].slice(0, 2).join(", ")}
          {ROLE_SCOPES[role].length > 2 ? ` +${ROLE_SCOPES[role].length - 2}` : ""}
        </span>
      </button>
      {open && children.length > 0 && (
        <div className="mt-0.5 mb-1">
          {children.map((c) => (
            <RoleNode key={c} role={c} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── GrantRoleModal ─────────────────────────────────────────────────────────────

interface GrantRoleModalProps {
  open: boolean;
  onClose: () => void;
}

function GrantRoleModal({ open, onClose }: GrantRoleModalProps): JSX.Element | null {
  const qc = useQueryClient();
  const [userId, setUserId] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [condition, setCondition] = useState("");

  const grant = useMutation({
    mutationFn: () =>
      rbacApi.createRole(
        userId.trim(),
        condition.trim() ? `${role}:${condition.trim()}` : role
      ),
    onSuccess: () => {
      toast({ kind: "success", message: "Role assigned successfully" });
      qc.invalidateQueries({ queryKey: ["rbac-roles"] });
      setUserId("");
      setCondition("");
      setRole("viewer");
      onClose();
    },
    onError: (e) => toast({ kind: "error", message: `Failed to assign role: ${String(e)}` }),
  });

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="grant-role-title"
    >
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />
      <div className="relative bg-card border border-border rounded-xl shadow-2xl max-w-md w-full p-6 space-y-5">

        {/* Title */}
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-full bg-primary/10">
            <Key className="h-4 w-4 text-primary" aria-hidden />
          </div>
          <h2 id="grant-role-title" className="text-base font-semibold">
            Grant Role
          </h2>
        </div>

        {/* User ID */}
        <div className="space-y-1.5">
          <label htmlFor="grant-user-id" className="text-xs font-medium text-muted-foreground">
            User ID or email
          </label>
          <input
            id="grant-user-id"
            autoFocus
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            placeholder="user@example.com or usr_abc123"
            className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>

        {/* Role picker */}
        <div className="space-y-2">
          <p className="text-xs font-medium text-muted-foreground">Select Role</p>
          <div className="grid grid-cols-2 gap-2">
            {ROLES.map((r) => (
              <button
                key={r}
                type="button"
                onClick={() => setRole(r)}
                className={`flex flex-col items-start gap-0.5 px-3 py-2.5 rounded-lg text-left border-2 transition-all ${
                  role === r
                    ? `${ROLE_PILL[r]} shadow-sm`
                    : "border-border hover:border-muted-foreground/40 hover:bg-muted/50"
                }`}
              >
                <span className="text-xs font-semibold capitalize">{r}</span>
                <span className="text-xs text-muted-foreground leading-tight">
                  {ROLE_SCOPES[r].slice(0, 2).join(", ")}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* Condition */}
        <div className="space-y-1.5">
          <label htmlFor="grant-condition" className="text-xs font-medium text-muted-foreground">
            Condition{" "}
            <span className="font-normal opacity-60">(optional)</span>
          </label>
          <input
            id="grant-condition"
            value={condition}
            onChange={(e) => setCondition(e.target.value)}
            placeholder='e.g. agent_id="abc123"'
            className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background font-mono focus:outline-none focus:ring-2 focus:ring-ring"
          />
          <p className="text-xs text-muted-foreground">
            Restrict this role to specific resources.
          </p>
        </div>

        {/* Actions */}
        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm border border-border rounded-md hover:bg-muted transition-colors"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => grant.mutate()}
            disabled={!userId.trim() || grant.isPending}
            className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {grant.isPending ? "Granting…" : "Grant Role"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── RbacPage ──────────────────────────────────────────────────────────────────

export function RbacPage(): JSX.Element {
  const qc = useQueryClient();

  // Tab
  const [tab, setTab] = useState<RbacTab>("roles");

  // Role Assignments state
  const [search, setSearch] = useState("");
  const [showGrant, setShowGrant] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [deleteRoleId, setDeleteRoleId] = useState<string | null>(null);
  const [showBulkConfirm, setShowBulkConfirm] = useState(false);

  // IP Allowlist state
  const [cidr, setCidr] = useState("");
  const [cidrDesc, setCidrDesc] = useState("");
  const [cidrError, setCidrError] = useState("");
  const [deleteIpId, setDeleteIpId] = useState<string | null>(null);

  // Queries
  const rolesQ = useQuery({
    queryKey: ["rbac-roles"],
    queryFn: () => rbacApi.listRoles(),
  });
  const ipsQ = useQuery({
    queryKey: ["rbac-ip"],
    queryFn: () => rbacApi.listIpAllowlist(),
  });

  // Mutations — role
  const deleteRole = useMutation({
    mutationFn: (id: string) => rbacApi.deleteRole(id),
    onSuccess: () => {
      toast({ kind: "success", message: "Role assignment removed" });
      qc.invalidateQueries({ queryKey: ["rbac-roles"] });
      setDeleteRoleId(null);
      setSelectedIds((prev) => { const n = new Set(prev); n.delete(deleteRoleId!); return n; });
    },
    onError: (e) => toast({ kind: "error", message: `Failed: ${String(e)}` }),
  });

  const bulkDelete = useMutation({
    mutationFn: (ids: string[]) => Promise.all(ids.map((id) => rbacApi.deleteRole(id))),
    onSuccess: (_, ids) => {
      toast({ kind: "success", message: `${ids.length} assignment${ids.length !== 1 ? "s" : ""} removed` });
      qc.invalidateQueries({ queryKey: ["rbac-roles"] });
      setSelectedIds(new Set());
      setShowBulkConfirm(false);
    },
    onError: (e) => toast({ kind: "error", message: `Bulk delete failed: ${String(e)}` }),
  });

  // Mutations — IP
  const addIp = useMutation({
    mutationFn: () => rbacApi.addIpAllowlist(cidr.trim(), cidrDesc.trim()),
    onSuccess: () => {
      toast({ kind: "success", message: "CIDR added to allowlist" });
      setCidr("");
      setCidrDesc("");
      setCidrError("");
      qc.invalidateQueries({ queryKey: ["rbac-ip"] });
    },
    onError: (e) => toast({ kind: "error", message: `Failed to add CIDR: ${String(e)}` }),
  });

  const deleteIp = useMutation({
    mutationFn: (id: string) => rbacApi.deleteIpAllowlist(id),
    onSuccess: () => {
      toast({ kind: "success", message: "CIDR entry removed" });
      qc.invalidateQueries({ queryKey: ["rbac-ip"] });
      setDeleteIpId(null);
    },
    onError: (e) => toast({ kind: "error", message: `Failed: ${String(e)}` }),
  });

  // Derived — stable references (avoid ??\u00a0[] creating a new array each render)
  const allRoles = useMemo<RoleAssignment[]>(() => rolesQ.data ?? [], [rolesQ.data]);
  const allIps = useMemo<IpAllowlistEntry[]>(() => ipsQ.data ?? [], [ipsQ.data]);

  const filteredRoles = useMemo(() => {
    const q = search.toLowerCase().trim();
    if (!q) return allRoles;
    return allRoles.filter(
      (r) => r.user_id.toLowerCase().includes(q) || r.role.toLowerCase().includes(q)
    );
  }, [allRoles, search]);

  const roleCounts = useMemo<Record<Role, number>>(() => {
    const c = { admin: 0, approver: 0, operator: 0, viewer: 0 } as Record<Role, number>;
    for (const r of allRoles) c[resolveRoleKey(r.role)]++;
    return c;
  }, [allRoles]);

  const allSelected =
    filteredRoles.length > 0 && selectedIds.size === filteredRoles.length;
  const someSelected = selectedIds.size > 0 && !allSelected;

  // Handlers
  const handleAddIp = (): void => {
    if (!isValidCidr(cidr.trim())) {
      setCidrError("Invalid CIDR — use IPv4 notation, e.g. 10.0.0.0/8");
      return;
    }
    setCidrError("");
    addIp.mutate();
  };

  const toggleSelect = (id: string): void =>
    setSelectedIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const toggleSelectAll = (): void =>
    setSelectedIds(
      allSelected ? new Set() : new Set(filteredRoles.map((r) => r.id))
    );

  // Tab styles
  const TAB_BASE = "flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors";
  const TAB_ON = `${TAB_BASE} border-primary text-foreground`;
  const TAB_OFF = `${TAB_BASE} border-transparent text-muted-foreground hover:text-foreground`;

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto space-y-6">

      {/* ── Page header ──────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Access Control</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Manage team roles, permissions, and network allowlists.
          </p>
        </div>
        {tab === "roles" && (
          <button
            data-testid="grant-role-btn"
            onClick={() => setShowGrant(true)}
            className="flex items-center gap-1.5 px-3.5 py-2 text-sm font-medium bg-primary text-primary-foreground rounded-lg hover:opacity-90 transition-opacity"
          >
            <Plus className="h-4 w-4" /> Grant Role
          </button>
        )}
      </div>

      {/* ── Tabs ─────────────────────────────────────────────────────────── */}
      <div className="border-b border-border">
        <nav className="flex gap-1" aria-label="RBAC sections">
          <button
            data-testid="tab-roles"
            onClick={() => setTab("roles")}
            className={tab === "roles" ? TAB_ON : TAB_OFF}
          >
            <Users className="h-3.5 w-3.5" />
            Role Assignments
            {!rolesQ.isLoading && allRoles.length > 0 && (
              <span className="ml-1 px-1.5 py-0.5 text-xs rounded-full bg-muted text-muted-foreground font-mono">
                {allRoles.length}
              </span>
            )}
          </button>
          <button
            data-testid="tab-ip"
            onClick={() => setTab("ip")}
            className={tab === "ip" ? TAB_ON : TAB_OFF}
          >
            <Network className="h-3.5 w-3.5" />
            IP Allowlist
            {!ipsQ.isLoading && allIps.length > 0 && (
              <span className="ml-1 px-1.5 py-0.5 text-xs rounded-full bg-muted text-muted-foreground font-mono">
                {allIps.length}
              </span>
            )}
          </button>
        </nav>
      </div>

      {/* ════════════════════════════════════════════════════════════════════
          Roles Tab
      ════════════════════════════════════════════════════════════════════ */}
      {tab === "roles" && (
        <div className="space-y-5">

          {/* Stats row */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {ROLES.map((r) => (
              <div
                key={r}
                className="bg-card border border-border rounded-xl p-4 flex items-center gap-3"
              >
                <div className={`p-2.5 rounded-full shrink-0 ${ROLE_ICON_BG[r]}`}>
                  <Shield className="h-3.5 w-3.5" aria-hidden />
                </div>
                <div className="min-w-0">
                  <p className="text-xs text-muted-foreground capitalize leading-none mb-1.5">
                    {r}
                  </p>
                  {rolesQ.isLoading ? (
                    <Skeleton className="h-5 w-8" />
                  ) : (
                    <p className="text-xl font-bold leading-none tabular-nums">
                      {roleCounts[r]}
                    </p>
                  )}
                </div>
              </div>
            ))}
          </div>

          {/* Search + bulk toolbar */}
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative flex-1 min-w-[180px] max-w-sm">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none" />
              <input
                value={search}
                onChange={(e) => { setSearch(e.target.value); setSelectedIds(new Set()); }}
                placeholder="Search by user or role…"
                className="w-full pl-9 pr-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
            {selectedIds.size > 0 && (
              <button
                onClick={() => setShowBulkConfirm(true)}
                className="flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-red-600 border border-red-200 bg-red-50 rounded-lg hover:bg-red-100 dark:bg-red-900/20 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-900/40 transition-colors"
              >
                <Trash2 className="h-3.5 w-3.5" />
                Remove {selectedIds.size} selected
              </button>
            )}
          </div>

          {/* Roles table */}
          {rolesQ.isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full rounded-lg" />
              ))}
            </div>
          ) : filteredRoles.length === 0 ? (
            <EmptyState
              title={search ? "No matching assignments" : "No role assignments yet"}
              description={
                search
                  ? "Try a different search term."
                  : "Grant a role to give a team member access."
              }
              action={
                !search ? (
                  <button
                    onClick={() => setShowGrant(true)}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-md hover:opacity-90"
                  >
                    <Plus className="h-3.5 w-3.5" /> Grant Role
                  </button>
                ) : undefined
              }
            />
          ) : (
            <div
              data-testid="roles-table"
              className="rounded-xl border border-border overflow-hidden"
            >
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-muted/50 border-b border-border text-xs text-muted-foreground">
                    <th className="w-10 px-3 py-3 text-left">
                      <input
                        type="checkbox"
                        checked={allSelected}
                        ref={(el) => { if (el) el.indeterminate = someSelected; }}
                        onChange={toggleSelectAll}
                        className="rounded border-input cursor-pointer"
                        aria-label="Select all"
                      />
                    </th>
                    <th className="px-4 py-3 text-left font-medium">User</th>
                    <th className="px-4 py-3 text-left font-medium">Role</th>
                    <th className="px-4 py-3 text-left font-medium hidden md:table-cell">
                      Created
                    </th>
                    <th className="px-4 py-3 text-right font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {filteredRoles.map((r) => {
                    const roleKey = resolveRoleKey(r.role);
                    const checked = selectedIds.has(r.id);
                    return (
                      <tr
                        key={r.id}
                        className={`group transition-colors hover:bg-muted/30 ${
                          checked ? "bg-primary/5 dark:bg-primary/10" : ""
                        }`}
                      >
                        <td className="px-3 py-3">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleSelect(r.id)}
                            className="rounded border-input cursor-pointer"
                            aria-label={`Select ${r.user_id}`}
                          />
                        </td>
                        <td className="px-4 py-3 font-mono text-xs">{r.user_id}</td>
                        <td className="px-4 py-3">
                          <span
                            className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-semibold ${ROLE_PILL[roleKey]}`}
                          >
                            {r.role}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-xs text-muted-foreground hidden md:table-cell">
                          {fmtDate(r.created_at)}
                        </td>
                        <td className="px-4 py-3 text-right">
                          <button
                            data-testid={`delete-role-${r.id}`}
                            onClick={() => setDeleteRoleId(r.id)}
                            aria-label={`Remove role for ${r.user_id}`}
                            className="p-1.5 rounded-md text-muted-foreground hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 opacity-0 group-hover:opacity-100 focus:opacity-100 transition-all"
                          >
                            <Trash2 className="h-3.5 w-3.5" aria-hidden />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* Bottom cards: hierarchy tree + role summary */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">

            {/* Hierarchy tree */}
            <div className="bg-card border border-border rounded-xl p-5 space-y-3">
              <div className="flex items-center gap-2">
                <Shield className="h-4 w-4 text-muted-foreground" aria-hidden />
                <h3 className="text-sm font-semibold">Role Hierarchy</h3>
              </div>
              <p className="text-xs text-muted-foreground">
                Higher roles inherit permissions from roles beneath them.
              </p>
              <RoleNode role="admin" />
            </div>

            {/* Role summary */}
            <div className="bg-card border border-border rounded-xl p-5 space-y-3">
              <div className="flex items-center gap-2">
                <CheckCircle2 className="h-4 w-4 text-muted-foreground" aria-hidden />
                <h3 className="text-sm font-semibold">Role Summary</h3>
              </div>
              <div className="space-y-2.5">
                {ROLES.map((r) => (
                  <div key={r} className="flex items-start gap-2.5">
                    <span
                      className={`mt-0.5 px-2 py-0.5 rounded-full text-xs font-semibold shrink-0 ${ROLE_PILL[r]}`}
                    >
                      {r}
                    </span>
                    <span className="text-xs text-muted-foreground leading-relaxed">
                      {ROLE_DESCRIPTIONS[r]}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ════════════════════════════════════════════════════════════════════
          IP Allowlist Tab
      ════════════════════════════════════════════════════════════════════ */}
      {tab === "ip" && (
        <div className="space-y-5">

          {/* Info banner */}
          <div className="flex items-start gap-3 p-4 rounded-xl bg-blue-50 border border-blue-200 dark:bg-blue-900/20 dark:border-blue-800 text-blue-700 dark:text-blue-300">
            <Network className="h-4 w-4 mt-0.5 shrink-0" aria-hidden />
            <div>
              <p className="text-sm font-medium">Network Allowlist</p>
              <p className="text-xs mt-0.5 opacity-80">
                When entries are present, only traffic originating from listed CIDRs is
                permitted. Remove all entries to allow any IP.
              </p>
            </div>
          </div>

          {/* Add CIDR form */}
          <div
            data-testid="ip-form"
            className="bg-card border border-border rounded-xl p-5 space-y-4"
          >
            <h3 className="text-sm font-semibold">Add IP Range</h3>
            <div className="grid grid-cols-1 sm:grid-cols-[auto_1fr_auto] gap-3 items-end">
              <div className="space-y-1.5">
                <label
                  htmlFor="cidr-input"
                  className="text-xs font-medium text-muted-foreground"
                >
                  CIDR Block
                </label>
                <input
                  id="cidr-input"
                  value={cidr}
                  onChange={(e) => { setCidr(e.target.value); setCidrError(""); }}
                  onKeyDown={(e) => e.key === "Enter" && handleAddIp()}
                  placeholder="10.0.0.0/8"
                  className={`px-3 py-2 border rounded-lg bg-background text-sm font-mono w-full sm:w-44 focus:outline-none focus:ring-2 focus:ring-ring ${
                    cidrError
                      ? "border-red-400 focus:ring-red-300"
                      : "border-input"
                  }`}
                />
                {cidrError && (
                  <p className="text-xs text-red-500">{cidrError}</p>
                )}
              </div>
              <div className="space-y-1.5">
                <label
                  htmlFor="cidr-desc"
                  className="text-xs font-medium text-muted-foreground"
                >
                  Description
                </label>
                <input
                  id="cidr-desc"
                  value={cidrDesc}
                  onChange={(e) => setCidrDesc(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleAddIp()}
                  placeholder="Office network, VPN, CI cluster…"
                  className="w-full px-3 py-2 border border-input rounded-lg bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
              <button
                data-testid="add-cidr-btn"
                onClick={handleAddIp}
                disabled={!cidr.trim() || addIp.isPending}
                className="flex items-center gap-1.5 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap self-end"
              >
                <Plus className="h-3.5 w-3.5" />
                {addIp.isPending ? "Adding…" : "Add CIDR"}
              </button>
            </div>
          </div>

          {/* IP list */}
          {ipsQ.isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full rounded-lg" />
              ))}
            </div>
          ) : allIps.length === 0 ? (
            <EmptyState
              title="No allowlist entries"
              description="All IPs are allowed until the first entry is added."
            />
          ) : (
            <div
              data-testid="ip-list"
              className="rounded-xl border border-border overflow-hidden"
            >
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-muted/50 border-b border-border text-xs text-muted-foreground">
                    <th className="px-4 py-3 text-left font-medium">CIDR Block</th>
                    <th className="px-4 py-3 text-left font-medium">Description</th>
                    <th className="px-4 py-3 text-left font-medium hidden md:table-cell">
                      Added
                    </th>
                    <th className="px-4 py-3 text-right font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {allIps.map((entry) => (
                    <tr
                      key={entry.id}
                      className="group hover:bg-muted/30 transition-colors"
                    >
                      <td className="px-4 py-3">
                        <span className="font-mono text-xs bg-muted px-2 py-1 rounded">
                          {entry.cidr}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs text-muted-foreground">
                        {entry.description ? (
                          entry.description
                        ) : (
                          <span className="italic opacity-40">No description</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs text-muted-foreground hidden md:table-cell">
                        {fmtDate(entry.created_at)}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <button
                          data-testid={`delete-ip-${entry.id}`}
                          onClick={() => setDeleteIpId(entry.id)}
                          aria-label={`Remove CIDR ${entry.cidr}`}
                          className="p-1.5 rounded-md text-muted-foreground hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 opacity-0 group-hover:opacity-100 focus:opacity-100 transition-all"
                        >
                          <Trash2 className="h-3.5 w-3.5" aria-hidden />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── Modals ───────────────────────────────────────────────────────── */}
      <GrantRoleModal open={showGrant} onClose={() => setShowGrant(false)} />

      <ConfirmModal
        open={!!deleteRoleId}
        title="Remove role assignment?"
        description="The user will immediately lose this role's permissions."
        confirmLabel="Remove"
        variant="danger"
        isLoading={deleteRole.isPending}
        onConfirm={() => deleteRoleId && deleteRole.mutate(deleteRoleId)}
        onCancel={() => setDeleteRoleId(null)}
      />

      <ConfirmModal
        open={showBulkConfirm}
        title={`Remove ${selectedIds.size} assignment${selectedIds.size !== 1 ? "s" : ""}?`}
        description="All selected users will immediately lose their role permissions."
        confirmLabel={`Remove ${selectedIds.size}`}
        variant="danger"
        isLoading={bulkDelete.isPending}
        onConfirm={() => bulkDelete.mutate([...selectedIds])}
        onCancel={() => setShowBulkConfirm(false)}
      />

      <ConfirmModal
        open={!!deleteIpId}
        title="Remove CIDR entry?"
        description="Traffic from this IP range may be blocked depending on remaining entries."
        confirmLabel="Remove"
        variant="danger"
        isLoading={deleteIp.isPending}
        onConfirm={() => deleteIpId && deleteIp.mutate(deleteIpId)}
        onCancel={() => setDeleteIpId(null)}
      />
    </div>
  );
}

export default RbacPage;
