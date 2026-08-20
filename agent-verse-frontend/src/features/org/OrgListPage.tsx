/**
 * OrgListPage — browse and create AI Organizations.
 * Entry point for the AI Org OS. Lists all tenant orgs and allows creation.
 *
 * Skills: frontend-design, web-guidelines, emil-design-eng, impeccable-ui
 */
import { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Building2, Plus, ChevronRight, Activity } from 'lucide-react';
import { cn } from '@/lib/utils';
import { JARVISPageShell, JARVISStagger, JARVISStaggerItem } from '@/components/ui/JARVISPageShell';
import { useOrganizations, useCreateOrganization } from './hooks/useOrg';
import type { Organization } from './types';

const STATUS_DOT: Record<string, string> = {
  active:   'bg-emerald-400',
  paused:   'bg-amber-400',
  archived: 'bg-slate-500',
};

export function OrgListPage() {
  const reduce    = useReducedMotion();
  const navigate  = useNavigate();
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName]       = useState('');

  const { data: orgs, isLoading } = useOrganizations();
  const createOrg = useCreateOrganization();

  const orgList = (orgs as { data?: Organization[] } | undefined)?.data ?? [];

  const handleCreate = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newName.trim()) return;
    const org = await createOrg.mutateAsync({ name: newName.trim() });
    setNewName('');
    setShowCreate(false);
    navigate(`/org/${org.id}`);
  }, [newName, createOrg, navigate]);

  return (
    <JARVISPageShell className="max-w-3xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="flex items-start justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-[#F1F5F9] tracking-[-0.02em] [text-wrap:balance]">
            AI Organizations
          </h1>
          <p className="text-[14px] text-[#94A3B8] mt-1">
          {isLoading ? 'Loading…' : `${orgList.length} organization${orgList.length !== 1 ? 's' : ''}`}
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          aria-label="Create new organization"
          style={{ touchAction: 'manipulation' }}
          className={cn(
            'flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold',
            'bg-blue-600 hover:bg-blue-500 text-white',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400',
            'active:scale-[0.97] transition-[background-color,transform] duration-150',
            'min-h-[44px]',
          )}
        >
          <Plus className="h-4 w-4" aria-hidden />
          New Organization
        </button>
      </div>

      {/* Create form (inline slide-down) */}
      <AnimatePresence>
        {showCreate && (
          <motion.form
            key="create-form"
            initial={reduce ? { opacity: 0 } : { height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={reduce ? { opacity: 0 } : { height: 0, opacity: 0 }}
            transition={reduce ? { duration: 0.1 } : { type: 'spring', stiffness: 300, damping: 28 }}
            style={{ overflow: 'hidden' }}
            onSubmit={handleCreate}
            className="mb-6"
          >
            <div className="flex gap-3 p-4 bg-[#1A1F2E] border border-[#2D3748] rounded-xl">
              <input
                autoFocus
                type="text"
                value={newName}
                onChange={e => setNewName(e.target.value)}
                placeholder="e.g. Acme AI Operations…"
                aria-label="Organization name"
                aria-required="true"
                className={cn(
                  'flex-1 px-3 py-2 rounded-lg text-[14px]',
                  'bg-[#252B3B] border border-[#2D3748] text-[#F1F5F9]',
                  'placeholder:text-[#475569]',
                  'focus:outline-none focus:ring-2 focus:ring-blue-500/60',
                )}
              />
              <button
                type="submit"
                disabled={!newName.trim() || createOrg.isPending}
                style={{ touchAction: 'manipulation' }}
                className={cn(
                  'px-4 py-2 rounded-lg text-sm font-medium',
                  'bg-blue-600 hover:bg-blue-500 text-white',
                  'disabled:opacity-40',
                  'active:scale-[0.97] transition-[background-color,transform] duration-150',
                )}
              >
                {createOrg.isPending ? 'Creating…' : 'Create'}
              </button>
              <button
                type="button"
                onClick={() => { setShowCreate(false); setNewName(''); }}
                className="px-3 py-2 rounded-lg text-[#94A3B8] hover:text-[#F1F5F9] hover:bg-[#252B3B] text-sm transition-colors"
                aria-label="Cancel"
              >
                Cancel
              </button>
            </div>
          </motion.form>
        )}
      </AnimatePresence>

      {/* Org list */}
      {isLoading ? (
        <SkeletonList />
      ) : orgList.length === 0 ? (
        <EmptyState onCreate={() => setShowCreate(true)} />
      ) : (
        <JARVISStagger className="space-y-3" staggerMs={60}>
          {orgList.map((org: Organization) => (
            <JARVISStaggerItem interactive key={org.id}>
              <OrgCard org={org} onClick={() => navigate(`/org/${org.id}`)} />
            </JARVISStaggerItem>
          ))}
        </JARVISStagger>
      )}
    </JARVISPageShell>
  );
}

function OrgCard({ org, onClick }: { org: Organization; onClick: () => void }) {
  const dot = STATUS_DOT[org.status] ?? STATUS_DOT.active;
  return (
    <button
      onClick={onClick}
      aria-label={`Open ${org.name} organization`}
      style={{ touchAction: 'manipulation' }}
      className={cn(
        'w-full flex items-center gap-4 p-4 rounded-xl text-left',
        'bg-[#1A1F2E] border border-[#1E2535]',
        'hover:bg-[#1E2535] hover:border-[#2D3748]',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
        'active:scale-[0.99] transition-[background-color,border-color,transform] duration-150',
        'group',
      )}
    >
      {/* Icon */}
      <div className="h-10 w-10 rounded-xl bg-blue-500/10 border border-blue-500/20 flex items-center justify-center shrink-0">
        <Building2 className="h-5 w-5 text-[#00D4FF]" aria-hidden />
      </div>

      {/* Name + status (impeccable-ui: name dominant, meta secondary) */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-[15px] font-semibold text-[#F1F5F9] tracking-[-0.01em] truncate">
            {org.name}
          </span>
          <span
            aria-label={`Status: ${org.status}`}
            className={cn('h-1.5 w-1.5 rounded-full shrink-0', dot)}
          />
        </div>
        <p className="text-[12px] text-[#475569] mt-0.5 capitalize">{org.status}</p>
      </div>

      {/* Meta */}
      <div className="flex items-center gap-3 shrink-0 text-[12px] text-[#475569]">
        {org.industry && (
          <span className="hidden sm:block">{org.industry}</span>
        )}
        <ChevronRight
          className="h-4 w-4 text-[#475569] group-hover:text-[#94A3B8] transition-colors"
          aria-hidden
        />
      </div>
    </button>
  );
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center py-20 text-center">
      <div className="h-16 w-16 rounded-2xl bg-blue-500/10 border border-blue-500/20 flex items-center justify-center mb-5">
        <Activity className="h-7 w-7 text-[#00D4FF]" aria-hidden />
      </div>
      <h2 className="text-[17px] font-semibold text-[#F1F5F9] tracking-[-0.01em] mb-2">
        No organizations yet
      </h2>
      <p className="text-[14px] text-[#94A3B8] mb-6 max-w-xs">
        Create your first AI Organization to start deploying autonomous agents.
      </p>
      <button
        onClick={onCreate}
        style={{ touchAction: 'manipulation' }}
        className={cn(
          'flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-semibold',
          'bg-blue-600 hover:bg-blue-500 text-white',
          'active:scale-[0.97] transition-[background-color,transform] duration-150',
        )}
        aria-label="Create your first organization"
      >
        <Plus className="h-4 w-4" aria-hidden />
        Create Organization
      </button>
    </div>
  );
}

function SkeletonList() {
  return (
    <div className="space-y-3 animate-pulse">
      {[1, 2, 3].map(i => (
        <div key={i} className="flex items-center gap-4 p-4 rounded-xl bg-[#1A1F2E] border border-[#1E2535]">
          <div className="h-10 w-10 rounded-xl bg-[#252B3B] shrink-0" />
          <div className="flex-1 space-y-2">
            <div className="h-4 w-48 rounded bg-[#252B3B]" />
            <div className="h-3 w-20 rounded bg-[#252B3B]" />
          </div>
        </div>
      ))}
    </div>
  );
}
