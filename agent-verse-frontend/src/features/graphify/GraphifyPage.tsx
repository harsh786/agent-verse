/**
 * GraphifyPage — Standalone Graphify knowledge-graph builder page.
 *
 * Wraps the GraphifyProgress component with full JARVIS shell, org selector,
 * and live progress streaming. Graphify converts any org knowledge base into
 * an interactive glowing knowledge graph.
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Share2, Sparkles, Zap, Info } from 'lucide-react';
import { motion } from 'framer-motion';

import { JARVISPageShell, JARVISStagger, JARVISStaggerItem, SPRING_FAST } from '@/components/ui/JARVISPageShell';
import { GraphifyProgress } from '@/features/org/components/GraphifyProgress';

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

async function fetchOrgs(): Promise<{ id: string; name: string }[]> {
  const key = sessionStorage.getItem('av_api_key') ?? '';
  const r = await fetch(`${API_BASE}/v1/org/`, { headers: { 'X-API-Key': key } });
  if (!r.ok) return [];
  const d = await r.json();
  return Array.isArray(d) ? d : d.organizations ?? d.orgs ?? [];
}

export function GraphifyPage() {
  const [selectedOrg, setSelectedOrg] = useState<string | null>(null);
  const [running, setRunning]         = useState(false);
  const [done, setDone]               = useState(false);

  const { data: orgs = [] } = useQuery({
    queryKey: ['orgs-for-graphify'],
    queryFn: fetchOrgs,
  });

  return (
    <JARVISPageShell>
      <JARVISStagger className="space-y-6 max-w-5xl mx-auto">

        {/* ── Header ───────────────────────────────────────────────────── */}
        <JARVISStaggerItem>
          <div className="flex items-center gap-3">
            <motion.div
              className="p-3 rounded-2xl bg-[#00D4FF]/10 border border-[#00D4FF]/20"
              animate={{ boxShadow: ['0 0 12px rgba(0,212,255,0.2)', '0 0 28px rgba(0,212,255,0.5)', '0 0 12px rgba(0,212,255,0.2)'] }}
              transition={{ duration: 2.4, repeat: Infinity, ease: 'easeInOut' }}
            >
              <Share2 className="h-6 w-6 text-[#00D4FF]" />
            </motion.div>
            <div>
              <h1 className="text-2xl font-bold tracking-tight">Graphify</h1>
              <p className="text-sm text-muted-foreground mt-0.5">
                Transform your org's knowledge into a glowing, interactive knowledge graph
              </p>
            </div>
          </div>
        </JARVISStaggerItem>

        {/* ── Org selector ─────────────────────────────────────────────── */}
        <JARVISStaggerItem>
          <div className="bg-card border border-border rounded-2xl p-5 space-y-4">
            <div className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-[#00D4FF]" />
              <h2 className="text-sm font-semibold">Select Organisation</h2>
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
                    onClick={() => { setSelectedOrg(org.id); setDone(false); setRunning(false); }}
                    whileHover={{ scale: 1.02, boxShadow: '0 0 16px rgba(0,212,255,0.2)' }}
                    whileTap={{ scale: 0.98 }}
                    transition={SPRING_FAST}
                    className={`text-left p-3 rounded-xl border transition-colors ${
                      selectedOrg === org.id
                        ? 'border-[#00D4FF] bg-[#00D4FF]/10 text-[#00D4FF]'
                        : 'border-border bg-background hover:border-[#00D4FF]/40'
                    }`}
                  >
                    <Share2 className="h-4 w-4 mb-1.5 opacity-70" />
                    <p className="text-sm font-medium truncate">{org.name}</p>
                    <p className="text-xs text-muted-foreground truncate mt-0.5">{org.id.slice(0, 8)}…</p>
                  </motion.button>
                ))}
              </div>
            )}

            {selectedOrg && !running && !done && (
              <motion.button
                onClick={() => setRunning(true)}
                whileHover={{ scale: 1.02, boxShadow: '0 0 24px rgba(0,212,255,0.35)' }}
                whileTap={{ scale: 0.97 }}
                transition={SPRING_FAST}
                className="flex items-center gap-2 px-5 py-2.5 bg-[#00D4FF] text-black font-semibold rounded-xl text-sm"
              >
                <Zap className="h-4 w-4" />
                Start Graphify
              </motion.button>
            )}
          </div>
        </JARVISStaggerItem>

        {/* ── Graphify progress ────────────────────────────────────────── */}
        {selectedOrg && running && (
          <JARVISStaggerItem>
            <GraphifyProgress
              orgId={selectedOrg}
              onClose={() => { setRunning(false); }}
              onComplete={() => { setRunning(false); setDone(true); }}
            />
          </JARVISStaggerItem>
        )}

        {done && (
          <JARVISStaggerItem>
            <motion.div
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={SPRING_FAST}
              className="p-5 bg-green-500/10 border border-green-500/30 rounded-2xl text-center"
            >
              <div className="text-2xl mb-2">✅</div>
              <p className="text-sm font-semibold text-green-400">Knowledge graph built successfully!</p>
              <p className="text-xs text-muted-foreground mt-1">
                Navigate to the <strong>Knowledge Graph</strong> page to explore the glowing graph.
              </p>
              <motion.button
                onClick={() => setDone(false)}
                whileHover={{ scale: 1.02 }}
                transition={SPRING_FAST}
                className="mt-3 px-4 py-1.5 text-xs rounded-lg bg-[#00D4FF]/10 border border-[#00D4FF]/30 text-[#00D4FF]"
              >
                Run again
              </motion.button>
            </motion.div>
          </JARVISStaggerItem>
        )}

      </JARVISStagger>
    </JARVISPageShell>
  );
}

export default GraphifyPage;
