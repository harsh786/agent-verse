/**
 * ObsidianPage — Standalone Obsidian-style glowing knowledge vault page.
 *
 * Wraps ObsidianVaultExplorer with full JARVIS shell and org selector.
 * Renders knowledge nodes as an Obsidian-style glowing linked graph.
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Moon, Sparkles, Info, BookOpen } from 'lucide-react';
import { motion } from 'framer-motion';

import { JARVISPageShell, JARVISStagger, JARVISStaggerItem, SPRING_FAST } from '@/components/ui/JARVISPageShell';
import { ObsidianVaultExplorer } from '@/features/org/components/ObsidianVaultExplorer';

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

async function fetchOrgs(): Promise<{ id: string; name: string }[]> {
  const key = sessionStorage.getItem('av_api_key') ?? '';
  const r = await fetch(`${API_BASE}/v1/org/`, { headers: { 'X-API-Key': key } });
  if (!r.ok) return [];
  const d = await r.json();
  return Array.isArray(d) ? d : d.organizations ?? d.orgs ?? [];
}

export function ObsidianPage() {
  const [selectedOrg, setSelectedOrg] = useState<string | null>(null);

  const { data: orgs = [] } = useQuery({
    queryKey: ['orgs-for-obsidian'],
    queryFn: fetchOrgs,
  });

  return (
    <JARVISPageShell>
      <JARVISStagger className="space-y-6 max-w-6xl mx-auto">

        {/* ── Header ───────────────────────────────────────────────────── */}
        <JARVISStaggerItem>
          <div className="flex items-center gap-3">
            <motion.div
              className="p-3 rounded-2xl bg-violet-500/10 border border-violet-500/20"
              animate={{ boxShadow: ['0 0 12px rgba(139,92,246,0.2)', '0 0 28px rgba(139,92,246,0.5)', '0 0 12px rgba(139,92,246,0.2)'] }}
              transition={{ duration: 2.8, repeat: Infinity, ease: 'easeInOut' }}
            >
              <Moon className="h-6 w-6 text-violet-400" />
            </motion.div>
            <div>
              <h1 className="text-2xl font-bold tracking-tight">Obsidian Mode</h1>
              <p className="text-sm text-muted-foreground mt-0.5">
                Explore your org's knowledge vault as a glowing, linked note graph
              </p>
            </div>
          </div>
        </JARVISStaggerItem>

        {/* ── Org selector ─────────────────────────────────────────────── */}
        {!selectedOrg && (
          <JARVISStaggerItem>
            <div className="bg-card border border-border rounded-2xl p-5 space-y-4">
              <div className="flex items-center gap-2">
                <Sparkles className="h-4 w-4 text-violet-400" />
                <h2 className="text-sm font-semibold">Select Organisation Vault</h2>
              </div>

              {orgs.length === 0 ? (
                <div className="flex items-center gap-2 p-3 bg-amber-500/10 border border-amber-500/20 rounded-xl text-amber-400 text-sm">
                  <Info className="h-4 w-4 shrink-0" />
                  <span>No organisations found. Create an org first via the <strong>Organizations</strong> section.</span>
                </div>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
                  {orgs.map((org) => (
                    <motion.button
                      key={org.id}
                      onClick={() => setSelectedOrg(org.id)}
                      whileHover={{ scale: 1.02, boxShadow: '0 0 16px rgba(139,92,246,0.25)' }}
                      whileTap={{ scale: 0.98 }}
                      transition={SPRING_FAST}
                      className="text-left p-3 rounded-xl border border-border bg-background hover:border-violet-500/40 transition-colors"
                    >
                      <BookOpen className="h-4 w-4 mb-1.5 text-violet-400 opacity-70" />
                      <p className="text-sm font-medium truncate">{org.name}</p>
                      <p className="text-xs text-muted-foreground truncate mt-0.5">Open vault →</p>
                    </motion.button>
                  ))}
                </div>
              )}
            </div>
          </JARVISStaggerItem>
        )}

        {/* ── Obsidian vault explorer ───────────────────────────────────── */}
        {selectedOrg && (
          <JARVISStaggerItem>
            <div className="bg-card border border-border rounded-2xl overflow-hidden">
              <div className="flex items-center justify-between px-5 py-3 border-b border-border">
                <div className="flex items-center gap-2">
                  <Moon className="h-4 w-4 text-violet-400" />
                  <span className="text-sm font-semibold">Obsidian Vault</span>
                </div>
                <button
                  onClick={() => setSelectedOrg(null)}
                  className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                >
                  Change org
                </button>
              </div>
              <ObsidianVaultExplorer
                orgId={selectedOrg}
                onOpenCanvas={(canvasId) => {
                  console.log('Open canvas:', canvasId);
                }}
              />
            </div>
          </JARVISStaggerItem>
        )}

      </JARVISStagger>
    </JARVISPageShell>
  );
}

export default ObsidianPage;
