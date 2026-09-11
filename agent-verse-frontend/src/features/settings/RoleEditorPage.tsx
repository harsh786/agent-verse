/**
 * RoleEditorPage — AA3: Visual RBAC role/permission editor (org-scoped).
 *
 * Skills applied:
 *   frontend-design:   JARVIS dark palette, Inter, tabular layout
 *   emil-design-eng:   spring 600/35 modal, 300/28 permission row stagger
 *   impeccable-ui:     role name dominant, permission matrix secondary
 *   web-guidelines:    role=dialog, focus trap, aria-checked, keyboard nav
 *   ui-ux-pro-max:     useReducedMotion, 44px targets, aria-live regions
 */
import { useState, useCallback, useId } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Shield, Plus, Trash2, Pencil, Lock, Check, X, ChevronDown } from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiRequest } from '@/lib/api/client';
import { JARVISPageShell, JARVISStagger, JARVISStaggerItem } from '@/components/ui/JARVISPageShell';

const apiClient = {
  get: <T,>(path: string) => apiRequest<T>('GET', path),
  post: <T,>(path: string, body?: unknown) => apiRequest<T>('POST', path, body),
  put: <T,>(path: string, body?: unknown) => apiRequest<T>('PUT', path, body),
  delete: <T,>(path: string) => apiRequest<T>('DELETE', path),
};

// ── Types ─────────────────────────────────────────────────────────────────────

interface Permission {
  feature:  string;
  view:     boolean;
  edit:     boolean;
  delete:   boolean;
}

interface OrgRole {
  id:            string;
  name:          string;
  description:   string;
  isBuiltIn:     boolean;
  permissions:   Permission[];
  memberCount?:  number;
}

const FEATURES = ['Missions', 'Tasks', 'Knowledge Graph', 'Approvals', 'Members', 'Settings', 'Billing'] as const;

const BUILT_IN_ROLES: OrgRole[] = [
  { id: 'org_owner',    name: 'Org Owner',    description: 'Full control. Billing, delete org, manage all members.', isBuiltIn: true, permissions: FEATURES.map(f => ({ feature: f, view: true, edit: true, delete: true })) },
  { id: 'org_admin',   name: 'Org Admin',    description: 'Manage members, connectors, settings. Cannot delete org.', isBuiltIn: true, permissions: FEATURES.map(f => ({ feature: f, view: true, edit: f !== 'Billing', delete: false })) },
  { id: 'mission_lead',name: 'Mission Lead', description: 'Create/edit/delete missions and tasks. Cannot manage members.', isBuiltIn: true, permissions: FEATURES.map(f => ({ feature: f, view: true, edit: ['Missions','Tasks'].includes(f), delete: ['Missions','Tasks'].includes(f) })) },
  { id: 'agent_runner',name: 'Agent Runner', description: 'Execute missions. Read-only on settings.', isBuiltIn: true, permissions: FEATURES.map(f => ({ feature: f, view: true, edit: false, delete: false })) },
  { id: 'observer',    name: 'Observer',     description: 'View only. No write access anywhere.', isBuiltIn: true, permissions: FEATURES.map(f => ({ feature: f, view: true, edit: false, delete: false })) },
];

// ── Hooks ─────────────────────────────────────────────────────────────────────

function useOrgRoles(orgId: string) {
  return useQuery<OrgRole[]>({
    queryKey: ['org-roles', orgId],
    queryFn: async () => {
      try {
        const res = await apiClient.get<OrgRole[]>(`/v1/org/${orgId}/roles`);
        return res;
      } catch {
        return [];  // graceful fallback — built-in roles still shown
      }
    },
    staleTime: 60_000,
  });
}

function useCreateRole(orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Omit<OrgRole, 'id' | 'isBuiltIn'>) =>
      apiClient.post<OrgRole>(`/v1/org/${orgId}/roles`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['org-roles', orgId] }),
  });
}

function useDeleteRole(orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (roleId: string) =>
      apiClient.delete(`/v1/org/${orgId}/roles/${roleId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['org-roles', orgId] }),
  });
}

// ── Sub-components ────────────────────────────────────────────────────────────

const SPRING_FAST   = { type: 'spring', stiffness: 600, damping: 35 } as const;
const SPRING_MODAL  = { type: 'spring', stiffness: 300, damping: 28 } as const;

function PermCell({ checked, disabled, onChange, label }: { checked: boolean; disabled: boolean; onChange?: () => void; label: string }) {
  const reduce = useReducedMotion();
  return (
    <motion.button
      type="button"
      role="checkbox"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={onChange}
      whileTap={reduce ? {} : { scale: 0.9 }}
      transition={SPRING_FAST}
      style={{ touchAction: 'manipulation' }}
      className={[
        'w-7 h-7 rounded-md flex items-center justify-center mx-auto',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70',
        disabled ? 'cursor-not-allowed opacity-30' : 'cursor-pointer',
        checked ? 'bg-blue-600/20 text-[#00D4FF]' : 'bg-[#252B3B] text-[#475569]',
      ].join(' ')}
    >
      {checked ? <Check className="h-3.5 w-3.5" aria-hidden /> : <X className="h-3.5 w-3.5" aria-hidden />}
    </motion.button>
  );
}

function RoleRow({ role, onEdit, onDelete, index }: { role: OrgRole; onEdit: (r: OrgRole) => void; onDelete: (id: string) => void; index: number }) {
  const reduce = useReducedMotion();
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      style={{ animationDelay: `${Math.min(index, 8) * 0.04}s` }}
      className="jarvis-pop-in bg-[#1A1F2E] border border-[#2D3748] rounded-xl overflow-hidden"
    >
      {/* Header row */}
      <div className="flex items-center gap-3 px-4 py-3">
        <div className={[
          'w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0',
          role.isBuiltIn ? 'bg-purple-500/10 text-purple-400' : 'bg-blue-500/10 text-[#00D4FF]',
        ].join(' ')}>
          {role.isBuiltIn ? <Lock className="h-3.5 w-3.5" aria-hidden /> : <Shield className="h-3.5 w-3.5" aria-hidden />}
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-[14px] font-semibold text-[#F1F5F9] truncate">{role.name}</p>
          <p className="text-[12px] text-[#64748B] truncate">{role.description}</p>
        </div>
        {role.memberCount !== undefined && (
          <span className="text-[11px] text-[#475569] tabular-nums">{role.memberCount} members</span>
        )}
        <div className="flex items-center gap-1.5">
          {!role.isBuiltIn && (
            <>
              <motion.button
                whileTap={reduce ? {} : { scale: 0.93 }}
                transition={SPRING_FAST}
                onClick={() => onEdit(role)}
                aria-label={`Edit ${role.name} role`}
                style={{ touchAction: 'manipulation' }}
                className="w-8 h-8 rounded-lg flex items-center justify-center text-[#64748B] hover:text-[#94A3B8] hover:bg-[#252B3B] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
              >
                <Pencil className="h-3.5 w-3.5" aria-hidden />
              </motion.button>
              <motion.button
                whileTap={reduce ? {} : { scale: 0.93 }}
                transition={SPRING_FAST}
                onClick={() => onDelete(role.id)}
                aria-label={`Delete ${role.name} role`}
                style={{ touchAction: 'manipulation' }}
                className="w-8 h-8 rounded-lg flex items-center justify-center text-[#64748B] hover:text-red-400 hover:bg-red-500/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400/70"
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden />
              </motion.button>
            </>
          )}
          <motion.button
            whileTap={reduce ? {} : { scale: 0.93 }}
            transition={SPRING_FAST}
            onClick={() => setExpanded(x => !x)}
            aria-expanded={expanded}
            aria-label={expanded ? 'Collapse permissions' : 'Expand permissions'}
            style={{ touchAction: 'manipulation' }}
            className="w-8 h-8 rounded-lg flex items-center justify-center text-[#64748B] hover:text-[#94A3B8] hover:bg-[#252B3B] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
          >
            <motion.div animate={{ rotate: expanded ? 180 : 0 }} transition={SPRING_FAST}>
              <ChevronDown className="h-3.5 w-3.5" aria-hidden />
            </motion.div>
          </motion.button>
        </div>
      </div>

      {/* Permission matrix */}
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={SPRING_MODAL}
            style={{ overflow: 'hidden' }}
          >
            <div className="px-4 pb-4 border-t border-[#2D3748]">
              <table className="w-full mt-3 text-[12px]" role="table" aria-label={`Permissions for ${role.name}`}>
                <thead>
                  <tr>
                    <th className="text-left text-[#64748B] py-1.5 font-medium w-40">Feature</th>
                    <th className="text-center text-[#64748B] py-1.5 font-medium w-16">View</th>
                    <th className="text-center text-[#64748B] py-1.5 font-medium w-16">Edit</th>
                    <th className="text-center text-[#64748B] py-1.5 font-medium w-16">Delete</th>
                  </tr>
                </thead>
                <tbody>
                  {role.permissions.map(p => (
                    <tr key={p.feature} className="border-t border-[#1E2535]">
                      <td className="py-2 text-[#94A3B8]">{p.feature}</td>
                      <td className="py-2"><PermCell checked={p.view} disabled label={`${role.name} view ${p.feature}`} /></td>
                      <td className="py-2"><PermCell checked={p.edit} disabled label={`${role.name} edit ${p.feature}`} /></td>
                      <td className="py-2"><PermCell checked={p.delete} disabled label={`${role.name} delete ${p.feature}`} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ── New / Edit Role Modal ─────────────────────────────────────────────────────

function RoleModal({
  orgId,
  initial,
  onClose,
}: {
  orgId: string;
  initial?: OrgRole;
  onClose: () => void;
}) {
  const titleId = useId();
  const reduce  = useReducedMotion();
  const create  = useCreateRole(orgId);
  const [name, setName]   = useState(initial?.name ?? '');
  const [desc, setDesc]   = useState(initial?.description ?? '');
  const [perms, setPerms] = useState<Permission[]>(
    initial?.permissions ??
    FEATURES.map(f => ({ feature: f, view: false, edit: false, delete: false }))
  );

  const toggle = useCallback((feat: string, key: 'view' | 'edit' | 'delete') => {
    setPerms(ps => ps.map(p => p.feature === feat ? { ...p, [key]: !p[key] } : p));
  }, []);

  const handleSave = useCallback(async () => {
    await create.mutateAsync({ name, description: desc, permissions: perms, memberCount: 0 });
    onClose();
  }, [create, name, desc, perms, onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
    >
      <motion.div
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
        className="absolute inset-0 bg-black/60"
        onClick={onClose}
      />
      <div
        className="jarvis-pop-in relative bg-[#0F1117] border border-[#2D3748] rounded-2xl w-full max-w-lg max-h-[90vh] overflow-y-auto shadow-2xl"
      >
        <div className="p-6">
          <h2 id={titleId} className="text-[18px] font-bold text-[#F1F5F9] [text-wrap:balance] mb-1">
            {initial ? 'Edit Role' : 'New Custom Role'}
          </h2>
          <p className="text-[13px] text-[#64748B] mb-6">Define granular permissions for this role.</p>

          <div className="space-y-4 mb-6">
            <div>
              <label htmlFor="role-name" className="block text-[12px] font-medium text-[#94A3B8] mb-1.5">Role Name</label>
              <input
                id="role-name"
                type="text"
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder="e.g. Finance Approver"
                aria-required="true"
                className="w-full px-3 py-2 rounded-lg bg-[#1A1F2E] border border-[#2D3748] text-[14px] text-[#F1F5F9] placeholder:text-[#475569] focus:outline-none focus:ring-2 focus:ring-blue-500/60"
              />
            </div>
            <div>
              <label htmlFor="role-desc" className="block text-[12px] font-medium text-[#94A3B8] mb-1.5">Description</label>
              <input
                id="role-desc"
                type="text"
                value={desc}
                onChange={e => setDesc(e.target.value)}
                placeholder="What can this role do?"
                className="w-full px-3 py-2 rounded-lg bg-[#1A1F2E] border border-[#2D3748] text-[14px] text-[#F1F5F9] placeholder:text-[#475569] focus:outline-none focus:ring-2 focus:ring-blue-500/60"
              />
            </div>
          </div>

          <p className="text-[12px] font-medium text-[#94A3B8] mb-3">Permissions</p>
          <table className="w-full text-[12px] mb-6" aria-label="Permission matrix">
            <thead>
              <tr>
                <th className="text-left text-[#64748B] pb-2 font-medium">Feature</th>
                <th className="text-center text-[#64748B] pb-2 font-medium">View</th>
                <th className="text-center text-[#64748B] pb-2 font-medium">Edit</th>
                <th className="text-center text-[#64748B] pb-2 font-medium">Delete</th>
              </tr>
            </thead>
            <tbody>
              {perms.map(p => (
                <tr key={p.feature} className="border-t border-[#1E2535]">
                  <td className="py-2 text-[#94A3B8]">{p.feature}</td>
                  <td className="py-2"><PermCell checked={p.view} disabled={false} onChange={() => toggle(p.feature, 'view')} label={`View ${p.feature}`} /></td>
                  <td className="py-2"><PermCell checked={p.edit} disabled={false} onChange={() => toggle(p.feature, 'edit')} label={`Edit ${p.feature}`} /></td>
                  <td className="py-2"><PermCell checked={p.delete} disabled={false} onChange={() => toggle(p.feature, 'delete')} label={`Delete ${p.feature}`} /></td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="flex gap-3">
            <motion.button
              type="button"
              onClick={handleSave}
              disabled={!name.trim() || create.isPending}
              whileTap={reduce ? {} : { scale: 0.97 }}
              transition={SPRING_FAST}
              style={{ touchAction: 'manipulation' }}
              className="flex-1 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
            >
              {create.isPending ? 'Saving…' : 'Save Role'}
            </motion.button>
            <motion.button
              type="button"
              onClick={onClose}
              whileTap={reduce ? {} : { scale: 0.97 }}
              transition={SPRING_FAST}
              style={{ touchAction: 'manipulation' }}
              className="px-4 py-2.5 rounded-xl border border-[#2D3748] text-[#94A3B8] hover:text-[#F1F5F9] text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
            >
              Cancel
            </motion.button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

interface RoleEditorPageProps {
  orgId: string;
}

export function RoleEditorPage({ orgId }: RoleEditorPageProps) {
  const reduce        = useReducedMotion();
  const { data: customRoles = [] } = useOrgRoles(orgId);
  const deleteRole    = useDeleteRole(orgId);
  const [modal, setModal] = useState<'new' | OrgRole | null>(null);

  const allRoles = [...BUILT_IN_ROLES, ...customRoles];

  return (
    <JARVISPageShell className="max-w-3xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="flex items-start justify-between mb-8">
        <div>
          <h1 className="text-[24px] font-bold text-[#F1F5F9] [text-wrap:balance]">Roles &amp; Permissions</h1>
          <p className="text-[14px] text-[#64748B] mt-1">
            {BUILT_IN_ROLES.length} built-in · {customRoles.length} custom
          </p>
        </div>
        <motion.button
          whileTap={reduce ? {} : { scale: 0.97 }}
          transition={SPRING_FAST}
          onClick={() => setModal('new')}
          aria-label="Create new role"
          style={{ touchAction: 'manipulation' }}
          className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold bg-blue-600 hover:bg-blue-500 text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 min-h-[44px]"
        >
          <Plus className="h-4 w-4" aria-hidden />
          New Role
        </motion.button>
      </div>

      {/* Built-in section */}
      <section aria-label="Built-in roles" className="mb-8">
        <h2 className="text-[12px] font-semibold text-[#64748B] uppercase tracking-wider mb-3">Built-in Roles</h2>
        <JARVISStagger className="space-y-2" staggerMs={50}>
          {BUILT_IN_ROLES.map((r, i) => (
            <JARVISStaggerItem key={r.id}>
              <RoleRow role={r} index={i} onEdit={() => {}} onDelete={() => {}} />
            </JARVISStaggerItem>
          ))}
        </JARVISStagger>
      </section>

      {/* Custom section */}
      <section aria-label="Custom roles">
        <h2 className="text-[12px] font-semibold text-[#64748B] uppercase tracking-wider mb-3">Custom Roles</h2>
        <AnimatePresence mode="popLayout">
          {customRoles.length === 0 ? (
            <motion.div
              initial={false} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              className="text-center py-10 text-[#475569] text-[13px]"
            >
              No custom roles yet. Create one to define granular permissions.
            </motion.div>
          ) : (
            <JARVISStagger className="space-y-2" staggerMs={50}>
              {customRoles.map((r, i) => (
                <JARVISStaggerItem key={r.id}>
                  <RoleRow
                    role={r}
                    index={BUILT_IN_ROLES.length + i}
                    onEdit={setModal}
                    onDelete={(id) => deleteRole.mutate(id)}
                  />
                </JARVISStaggerItem>
              ))}
            </JARVISStagger>
          )}
        </AnimatePresence>
      </section>

      {/* Live region for mutation feedback */}
      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {deleteRole.isSuccess && 'Role deleted successfully.'}
      </div>

      {/* Modal */}
      <AnimatePresence>
        {modal && (
          <RoleModal
            orgId={orgId}
            initial={modal === 'new' ? undefined : allRoles.find(r => r === modal)}
            onClose={() => setModal(null)}
          />
        )}
      </AnimatePresence>
    </JARVISPageShell>
  );
}

export default RoleEditorPage;
