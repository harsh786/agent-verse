import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { rbacApi } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { toast } from "@/stores/toast";

const ROLES = ["admin", "approver", "operator", "viewer"] as const;

export function RbacPage() {
  const qc = useQueryClient();
  const [userId, setUserId] = useState("");
  const [role, setRole] = useState<(typeof ROLES)[number]>("viewer");
  const [cidr, setCidr] = useState("");
  const [cidrDesc, setCidrDesc] = useState("");

  const roles = useQuery({ queryKey: ["rbac-roles"], queryFn: () => rbacApi.listRoles() });
  const ips = useQuery({ queryKey: ["rbac-ip"], queryFn: () => rbacApi.listIpAllowlist() });

  const createRole = useMutation({
    mutationFn: () => rbacApi.createRole(userId, role),
    onSuccess: () => {
      toast({ kind: "success", message: "Role assigned." });
      setUserId("");
      qc.invalidateQueries({ queryKey: ["rbac-roles"] });
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });
  const deleteRole = useMutation({
    mutationFn: (id: string) => rbacApi.deleteRole(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rbac-roles"] }),
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });
  const addIp = useMutation({
    mutationFn: () => rbacApi.addIpAllowlist(cidr, cidrDesc),
    onSuccess: () => {
      toast({ kind: "success", message: "CIDR added." });
      setCidr(""); setCidrDesc("");
      qc.invalidateQueries({ queryKey: ["rbac-ip"] });
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });
  const deleteIp = useMutation({
    mutationFn: (id: string) => rbacApi.deleteIpAllowlist(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rbac-ip"] }),
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-8">
      <div>
        <h1 className="text-2xl font-bold">Access Control</h1>
        <p className="text-muted-foreground text-sm mt-1">Manage team roles and network allowlists.</p>
      </div>

      {/* Roles */}
      <section className="space-y-3">
        <h2 className="font-semibold">Team roles</h2>
        <div className="flex flex-wrap gap-3 items-end bg-card border border-border rounded-xl p-4">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            User ID
            <input
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              className="px-2 py-1.5 border border-input rounded-md bg-background text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Role
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as (typeof ROLES)[number])}
              className="px-2 py-1.5 border border-input rounded-md bg-background text-sm"
            >
              {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </label>
          <button
            onClick={() => createRole.mutate()}
            disabled={!userId || createRole.isPending}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm hover:opacity-90 disabled:opacity-50"
          >
            Assign role
          </button>
        </div>
        {roles.isLoading ? (
          <Skeleton className="h-10 w-full" />
        ) : (roles.data ?? []).length === 0 ? (
          <EmptyState title="No role assignments" />
        ) : (
          <div className="space-y-2">
            {(roles.data ?? []).map((r) => (
              <div key={r.id} className="flex items-center justify-between bg-card border border-border rounded-lg px-4 py-2.5 text-sm">
                <span><span className="font-medium">{r.user_id}</span> — {r.role}</span>
                <button onClick={() => deleteRole.mutate(r.id)} aria-label={`Remove role ${r.id}`} className="p-1.5 rounded-md hover:bg-accent text-muted-foreground">
                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* IP allowlist */}
      <section className="space-y-3">
        <h2 className="font-semibold">IP allowlist</h2>
        <div className="flex flex-wrap gap-3 items-end bg-card border border-border rounded-xl p-4">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            CIDR
            <input
              aria-label="CIDR"
              value={cidr}
              onChange={(e) => setCidr(e.target.value)}
              placeholder="10.0.0.0/8"
              className="px-2 py-1.5 border border-input rounded-md bg-background text-sm font-mono"
            />
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
            onClick={() => addIp.mutate()}
            disabled={!cidr || addIp.isPending}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm hover:opacity-90 disabled:opacity-50"
          >
            Add CIDR
          </button>
        </div>
        {ips.isLoading ? (
          <Skeleton className="h-10 w-full" />
        ) : (ips.data ?? []).length === 0 ? (
          <EmptyState title="No allowlist entries" description="All IPs are allowed until an entry is added." />
        ) : (
          <div className="space-y-2">
            {(ips.data ?? []).map((e) => (
              <div key={e.id} className="flex items-center justify-between bg-card border border-border rounded-lg px-4 py-2.5 text-sm">
                <span><span className="font-mono">{e.cidr}</span>{e.description ? ` — ${e.description}` : ""}</span>
                <button onClick={() => deleteIp.mutate(e.id)} aria-label={`Remove CIDR ${e.id}`} className="p-1.5 rounded-md hover:bg-accent text-muted-foreground">
                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                </button>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

export default RbacPage;
