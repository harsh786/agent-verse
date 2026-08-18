/**
 * CommandCenter — the main JARVIS-style dashboard for an AI Organization.
 * This is the primary entry point for the AI Organization OS UI.
 *
 * Layout:
 *   Top:    OrgHealthWidget (metrics at a glance)
 *   Left:   MissionsList (live, virtualized)
 *   Right:  Activity feed + Department tree
 */
import { useState, useCallback } from 'react';
import { motion } from 'framer-motion';
import { Building2, Plus, RefreshCw } from 'lucide-react';
import { OrgHealthWidget } from './components/OrgHealthWidget';
import { MissionsList } from './components/MissionsList';
import { useOrganization, useCreateMission, useOrgEvents } from './hooks/useOrg';
import type { OrgMission } from './types';

interface CommandCenterProps {
  orgId: string;
}

const pageVariants = {
  hidden:  { opacity: 0, y: 8, filter: 'blur(4px)' },
  visible: { opacity: 1, y: 0, filter: 'blur(0px)' },
};

export function CommandCenter({ orgId }: CommandCenterProps) {
  const [missionFilter, setMissionFilter] = useState<string | undefined>();
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [newMissionTitle, setNewMissionTitle] = useState('');
  const [selectedMission, setSelectedMission] = useState<OrgMission | null>(null);

  const { data: org, isLoading: orgLoading } = useOrganization(orgId);
  const { data: events } = useOrgEvents(orgId);
  const createMission = useCreateMission(orgId);

  const handleCreateMission = useCallback(async () => {
    if (!newMissionTitle.trim()) return;
    await createMission.mutateAsync({
      org_id:   orgId,
      title:    newMissionTitle.trim(),
      priority: 'medium',
    });
    setNewMissionTitle('');
    setShowCreateForm(false);
  }, [orgId, newMissionTitle, createMission]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleCreateMission();
    }
    if (e.key === 'Escape') {
      setShowCreateForm(false);
      setNewMissionTitle('');
    }
  }, [handleCreateMission]);

  const STATUS_FILTERS = [
    { value: undefined,     label: 'All'       },
    { value: 'active',      label: 'Active'    },
    { value: 'queued',      label: 'Queued'    },
    { value: 'review',      label: 'Review'    },
    { value: 'completed',   label: 'Completed' },
  ];

  if (orgLoading) {
    return (
      <div className="min-h-screen bg-[var(--bg-base)] flex items-center justify-center" aria-label="Loading organization">
        <div className="flex flex-col items-center gap-3">
          <RefreshCw className="h-6 w-6 animate-spin text-[var(--accent-blue)]" aria-hidden="true" />
          <span className="text-sm text-[var(--text-muted)]">Loading organization…</span>
        </div>
      </div>
    );
  }

  return (
    <motion.div
      variants={pageVariants}
      initial="hidden"
      animate="visible"
      transition={{ type: 'spring', stiffness: 280, damping: 26 }}
      className="min-h-screen bg-[var(--bg-base)] text-[var(--text-primary)]"
      data-testid="command-center-loaded"
    >
      {/* Header */}
      <header className="border-b border-[var(--border)] bg-[var(--bg-primary)]/80 backdrop-blur-xl sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-1.5 rounded-lg bg-[var(--accent-blue)]/10">
              <Building2 className="h-4 w-4 text-[var(--accent-blue)]" aria-hidden="true" />
            </div>
            <div>
              <h1 className="text-sm font-semibold text-[var(--text-primary)]">
                {org?.name ?? 'AI Organization'}
              </h1>
              <p className="text-xs text-[var(--text-muted)]">
                {org?.industry} · {org?.jurisdiction}
              </p>
            </div>
          </div>
          <button
            onClick={() => setShowCreateForm(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--accent-blue)] text-white text-sm font-medium hover:opacity-90 transition-opacity"
            aria-label="Create new mission"
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            New Mission
          </button>
        </div>
      </header>

      {/* Main content */}
      <main id="main-content" className="max-w-7xl mx-auto px-4 sm:px-6 py-6 space-y-6" tabIndex={-1}>

        {/* Health metrics */}
        <section aria-labelledby="health-heading">
          <h2 id="health-heading" className="sr-only">Organization Health</h2>
          <OrgHealthWidget orgId={orgId} />
        </section>

        {/* Create mission form */}
        {showCreateForm && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="rounded-lg border border-[var(--accent-blue)]/30 bg-[var(--bg-secondary)] p-4"
            role="form"
            aria-label="Create new mission"
          >
            <h3 className="text-sm font-medium mb-3">New Mission</h3>
            <input
              type="text"
              id="mission-title"
              value={newMissionTitle}
              onChange={(e) => setNewMissionTitle(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Describe what this mission should achieve…"
              className="w-full bg-[var(--bg-elevated)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--accent-blue)]/50"
              aria-label="Mission title"
              aria-required="true"
              autoFocus
            />
            <div className="flex gap-2 mt-3">
              <button
                onClick={handleCreateMission}
                disabled={!newMissionTitle.trim() || createMission.isPending}
                className="px-3 py-1.5 rounded-lg bg-[var(--accent-blue)] text-white text-xs font-medium disabled:opacity-50 hover:opacity-90 transition-opacity"
                aria-label="Submit mission"
              >
                {createMission.isPending ? 'Creating…' : 'Create Mission'}
              </button>
              <button
                onClick={() => { setShowCreateForm(false); setNewMissionTitle(''); }}
                className="px-3 py-1.5 rounded-lg border border-[var(--border)] text-xs text-[var(--text-secondary)] hover:bg-[var(--bg-elevated)] transition-colors"
                aria-label="Cancel"
              >
                Cancel
              </button>
            </div>
          </motion.div>
        )}

        {/* Main grid */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Missions panel */}
          <section className="lg:col-span-2" aria-labelledby="missions-heading">
            <div className="flex items-center justify-between mb-4">
              <h2 id="missions-heading" className="text-sm font-semibold text-[var(--text-primary)]">
                Missions
              </h2>
              {/* Status filter tabs */}
              <div className="flex gap-1" role="tablist" aria-label="Mission status filter">
                {STATUS_FILTERS.map(({ value, label }) => (
                  <button
                    key={label}
                    role="tab"
                    aria-selected={missionFilter === value}
                    onClick={() => setMissionFilter(value)}
                    className={[
                      'px-2 py-1 rounded text-xs transition-colors',
                      missionFilter === value
                        ? 'bg-[var(--accent-blue)]/20 text-[var(--accent-blue)]'
                        : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)]',
                    ].join(' ')}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            <MissionsList
              orgId={orgId}
              statusFilter={missionFilter}
              onMissionClick={setSelectedMission}
              onCreateClick={() => setShowCreateForm(true)}
            />
          </section>

          {/* Activity feed */}
          <aside aria-labelledby="activity-heading">
            <h2 id="activity-heading" className="text-sm font-semibold text-[var(--text-primary)] mb-4">
              Activity Feed
            </h2>
            <div className="space-y-2" role="feed" aria-label="Organization activity">
              {(!events || events.length === 0) ? (
                <p className="text-xs text-[var(--text-muted)] py-4 text-center">
                  No recent activity
                </p>
              ) : (
                events.slice(0, 15).map((event) => (
                  <div
                    key={event.id}
                    className="flex gap-2 p-2 rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)]"
                    role="article"
                    aria-label={event.title}
                  >
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-[var(--text-primary)] truncate">{event.title}</p>
                      <p className="text-xs text-[var(--text-muted)] mt-0.5">
                        {new Date(event.created_at).toLocaleTimeString()}
                      </p>
                    </div>
                  </div>
                ))
              )}
            </div>
          </aside>
        </div>
      </main>

      {/* Mission detail drawer (placeholder) */}
      {selectedMission && (
        <div
          className="fixed inset-0 bg-black/40 z-20"
          onClick={() => setSelectedMission(null)}
          aria-hidden="true"
        />
      )}
    </motion.div>
  );
}
