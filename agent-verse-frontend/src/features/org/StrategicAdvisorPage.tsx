/**
 * StrategicAdvisorPage — P4: Weekly AI-generated strategic intelligence brief.
 *
 * Shows: accomplishments, risks, opportunities, recommendations, KPI trends.
 * On-demand generation + weekly scheduled via Celery Beat (Sundays 18:00).
 *
 * Skills:
 *   frontend-design:   JARVIS dark intelligence brief layout, McKinsey aesthetic
 *   emil-design-eng:   spring 280/26 section entrance, stagger 60ms
 *   impeccable-ui:     health status dominant, rec list secondary
 *   web-guidelines:    aria-live for generation, time[datetime]
 *   ui-ux-pro-max:     44px targets, useReducedMotion
 */
import { motion, useReducedMotion } from 'framer-motion';
import { Sparkles, TrendingUp, AlertTriangle, Lightbulb, CheckCircle2, RefreshCw, Calendar } from 'lucide-react';
import { JARVISPageShell, JARVISStagger, JARVISStaggerItem } from '@/components/ui/JARVISPageShell';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiRequest } from '@/lib/api/client';

// ── Types ─────────────────────────────────────────────────────────────────────

interface StrategicBrief {
  org_id:             string;
  org_name:           string;
  week_ending:        string;
  health_summary:     string;
  accomplishments:    string[];
  risks:              string[];
  opportunities:      string[];
  recommendations:    string[];
  generation_method:  'llm' | 'template';
  generated_at:       string;
}

// ── Hooks ─────────────────────────────────────────────────────────────────────

function useStrategicBrief(orgId: string) {
  return useQuery<StrategicBrief>({
    queryKey: ['strategic-brief', orgId],
    queryFn:  () => apiRequest<StrategicBrief>('GET', `/v1/org/${orgId}/brief/strategic`),
    staleTime: 4 * 60 * 60 * 1000,  // 4 hours
    retry: 1,
  });
}

function useRefreshBrief(orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiRequest<StrategicBrief>('GET', `/v1/org/${orgId}/brief/strategic`),
    onSuccess: (data) => qc.setQueryData(['strategic-brief', orgId], data),
  });
}

// ── Spring constants ──────────────────────────────────────────────────────────

const SPRING_FAST  = { type: 'spring', stiffness: 600, damping: 35 } as const;
const SPRING_PANEL = { type: 'spring', stiffness: 280, damping: 26 } as const;

// ── Section component ─────────────────────────────────────────────────────────

function BriefSection({
  icon: Icon,
  title,
  items,
  iconColor,
  bgColor,
  delay,
}: {
  icon:      React.ComponentType<{ className?: string }>;
  title:     string;
  items:     string[];
  iconColor: string;
  bgColor:   string;
  delay:     number;
}) {
  const reduce = useReducedMotion();
  if (!items.length) return null;
  return (
    <motion.div
      initial={reduce ? { opacity: 0 } : { opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ ...SPRING_PANEL, delay }}
      className="bg-[#1A1F2E] border border-[#2D3748] rounded-xl p-4"
    >
      <div className="flex items-center gap-2.5 mb-3">
        <div className={`w-7 h-7 rounded-lg flex items-center justify-center ${bgColor}`}>
          <Icon className={`h-3.5 w-3.5 ${iconColor}`} aria-hidden />
        </div>
        <h3 className="text-[13px] font-semibold text-[#F1F5F9]">{title}</h3>
      </div>
      <ul className="space-y-2">
        {items.map((item, i) => (
          <motion.li
            key={i}
            initial={reduce ? {} : { opacity: 0, x: -6 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ ...SPRING_FAST, delay: delay + i * 0.04 }}
            className="flex items-start gap-2 text-[13px] text-[#94A3B8]"
          >
            <span className={`w-1.5 h-1.5 rounded-full mt-1.5 flex-shrink-0 ${iconColor.replace('text-', 'bg-')}`} />
            {item}
          </motion.li>
        ))}
      </ul>
    </motion.div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

interface StrategicAdvisorPageProps {
  orgId:   string;
  orgName?: string;
}

export function StrategicAdvisorPage({ orgId, orgName }: StrategicAdvisorPageProps) {
  const reduce  = useReducedMotion();
  const { data: brief, isLoading } = useStrategicBrief(orgId);
  const refresh = useRefreshBrief(orgId);

  const healthColor = brief?.health_summary.includes('Healthy')
    ? 'text-emerald-400'
    : brief?.health_summary.includes('Attention')
    ? 'text-red-400'
    : 'text-amber-400';

  return (
    <JARVISPageShell className="max-w-3xl mx-auto px-6 py-8 space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2.5 mb-1">
            <div className="w-9 h-9 rounded-xl bg-purple-500/10 flex items-center justify-center">
              <Sparkles className="h-4.5 w-4.5 text-purple-400" aria-hidden />
            </div>
            <div>
              <h1 className="text-[22px] font-bold text-[#F1F5F9] [text-wrap:balance]">
                Strategic Advisor
              </h1>
              <p className="text-[12px] text-[#64748B]">
                {orgName ?? 'Organisation'} · Weekly intelligence brief
              </p>
            </div>
          </div>
        </div>
        <motion.button
          whileTap={reduce ? {} : { scale: 0.93 }} transition={SPRING_FAST}
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending || isLoading}
          aria-label="Regenerate strategic brief"
          style={{ touchAction: 'manipulation' }}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-[12px] text-[#64748B] hover:text-[#94A3B8] hover:bg-[#1A1F2E] border border-[#2D3748] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 min-h-[40px]"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${refresh.isPending ? 'animate-spin' : ''}`} aria-hidden />
          Regenerate
        </motion.button>
      </div>

      {isLoading ? (
        <div className="flex flex-col items-center py-16 gap-4" aria-live="polite">
          <motion.div animate={{ rotate: 360 }} transition={{ duration: 1.5, repeat: Infinity, ease: 'linear' }}>
            <Sparkles className="h-8 w-8 text-purple-400" aria-hidden />
          </motion.div>
          <p className="text-[14px] text-[#94A3B8]">Generating strategic brief…</p>
          <p className="text-[12px] text-[#475569]">Analysing performance, trends, and opportunities</p>
        </div>
      ) : brief ? (
        <>
          {/* Health banner */}
          <div className="bg-[#1A1F2E] border border-[#2D3748] rounded-xl p-4 flex items-start gap-3">
            <div>
              <div className="flex items-center gap-2 mb-1">
                <p className={`text-[16px] font-bold ${healthColor}`}>{brief.health_summary}</p>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#252B3B] text-[#64748B] font-medium">
                  {brief.generation_method === 'llm' ? 'AI-generated' : 'Template'}
                </span>
              </div>
              <div className="flex items-center gap-1.5 text-[11px] text-[#64748B]">
                <Calendar className="h-3.5 w-3.5" aria-hidden />
                <span>Week ending </span>
                <time dateTime={brief.week_ending} className="text-[#94A3B8]">{brief.week_ending}</time>
                <span>·</span>
                <span>Generated </span>
                <time dateTime={brief.generated_at} className="text-[#94A3B8] tabular-nums">
                  {new Intl.DateTimeFormat('en', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(brief.generated_at))}
                </time>
              </div>
            </div>
          </div>

          {/* Sections */}
          <JARVISStagger className="grid gap-3 sm:grid-cols-2" staggerMs={80}>
            <JARVISStaggerItem>
              <BriefSection
                icon={CheckCircle2}  title="Accomplishments"
                items={brief.accomplishments}
                iconColor="text-emerald-400" bgColor="bg-emerald-500/10" delay={0.05}
              />
            </JARVISStaggerItem>
            <JARVISStaggerItem>
              <BriefSection
                icon={AlertTriangle} title="Risks"
                items={brief.risks}
                iconColor="text-red-400" bgColor="bg-red-500/10" delay={0.1}
              />
            </JARVISStaggerItem>
            <JARVISStaggerItem>
              <BriefSection
                icon={TrendingUp}    title="Opportunities"
                items={brief.opportunities}
                iconColor="text-blue-400" bgColor="bg-blue-500/10" delay={0.15}
              />
            </JARVISStaggerItem>
            <JARVISStaggerItem>
              <BriefSection
                icon={Lightbulb}    title="Recommendations"
                items={brief.recommendations}
                iconColor="text-amber-400" bgColor="bg-amber-500/10" delay={0.2}
              />
            </JARVISStaggerItem>
          </JARVISStagger>
        </>
      ) : (
        <div className="text-center py-12 text-[#475569] text-[13px]">
          <Sparkles className="h-10 w-10 mx-auto mb-3 opacity-20" aria-hidden />
          No brief available. Click Regenerate to create one.
        </div>
      )}

      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {refresh.isSuccess && 'Strategic brief regenerated.'}
        {isLoading && 'Generating strategic brief…'}
      </div>
    </JARVISPageShell>
  );
}

export default StrategicAdvisorPage;
