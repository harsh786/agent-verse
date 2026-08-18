/**
 * DomainDetailPage — deep-dive view for a single domain vertical.
 *
 * Fetches:
 *   GET /marketplace/templates?domain=<key>  — agent templates for this domain
 *   GET /templates?domain=<key>              — goal templates for this domain
 *
 * Layout:
 *   - Breadcrumb + back link
 *   - Domain hero (icon, name, description, tags)
 *   - Stats strip: N agent templates | N goal templates
 *   - Two-column grid: Agent Templates | Goal Templates
 *   - Empty state with "Create Template" CTA when both sections are empty
 */
import { useState } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  Package,
  FileText,
  Zap,
  Loader2,
  Download,
  ShieldCheck,
  X,
  ChevronRight,
} from 'lucide-react';
import {
  marketplaceApi,
  templatesApi,
  type MarketplaceV2Template,
  type GoalTemplate,
} from '@/lib/api/client';
import { Skeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { TemplateCard } from '@/features/templates/components/TemplateCard';
import { TemplateInstantiator } from '@/features/templates/components/TemplateInstantiator';
import { toast } from '@/stores/toast';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';
// ── Domain metadata lookup ────────────────────────────────────────────────────

const DOMAIN_META: Record<
  string,
  { name: string; icon: string; description: string; color: string; tags: string[] }
> = {
  'hr-talent': {
    name: 'HR & Talent',
    icon: '👥',
    description: 'Hire, onboard, and retain faster with AI-powered workflows.',
    color: 'bg-blue-100 dark:bg-blue-900/20',
    tags: ['hiring', 'onboarding', 'HR automation'],
  },
  'software': {
    name: 'Software Engineering',
    icon: '💻',
    description: 'AI pair programmer for your whole team — from code review to deployment.',
    color: 'bg-purple-100 dark:bg-purple-900/20',
    tags: ['code review', 'CI/CD', 'debugging'],
  },
  'devops': {
    name: 'DevOps & SRE',
    icon: '⚙️',
    description: 'Zero-touch incident response and infrastructure automation.',
    color: 'bg-orange-100 dark:bg-orange-900/20',
    tags: ['incidents', 'monitoring', 'automation'],
  },
  'sales-crm': {
    name: 'Sales & CRM',
    icon: '📈',
    description: '10x pipeline without 10x headcount.',
    color: 'bg-green-100 dark:bg-green-900/20',
    tags: ['leads', 'pipeline', 'CRM'],
  },
  'operations': {
    name: 'Operations',
    icon: '🏭',
    description: 'Optimize procurement and logistics at every stage.',
    color: 'bg-yellow-100 dark:bg-yellow-900/20',
    tags: ['procurement', 'logistics', 'supply chain'],
  },
  'legal': {
    name: 'Legal',
    icon: '⚖️',
    description: 'Contract review at paralegal speed.',
    color: 'bg-slate-100 dark:bg-slate-900/20',
    tags: ['contracts', 'compliance', 'NDAs'],
  },
  'gst-tax': {
    name: 'GST & Tax',
    icon: '🧾',
    description: 'Automate every GSTR filing and reconciliation.',
    color: 'bg-red-100 dark:bg-red-900/20',
    tags: ['GSTR', 'reconciliation', 'tax filing'],
  },
  'invoicing-finance': {
    name: 'Invoicing & Finance',
    icon: '💰',
    description: '3-way match in seconds — PO, GRN, and invoice reconciliation.',
    color: 'bg-emerald-100 dark:bg-emerald-900/20',
    tags: ['invoicing', 'reconciliation', 'AP/AR'],
  },
  'government-portal': {
    name: 'Government Portal',
    icon: '🏛️',
    description: 'GeM, RTI, MSME filings on autopilot.',
    color: 'bg-indigo-100 dark:bg-indigo-900/20',
    tags: ['GeM', 'RTI', 'MSME'],
  },
  'e-commerce': {
    name: 'E-Commerce',
    icon: '🛒',
    description: 'Catalog, orders, and reviews automated end-to-end.',
    color: 'bg-pink-100 dark:bg-pink-900/20',
    tags: ['catalog', 'orders', 'reviews'],
  },
  'education': {
    name: 'Education',
    icon: '🎓',
    description: 'Personalized learning at scale for every student.',
    color: 'bg-cyan-100 dark:bg-cyan-900/20',
    tags: ['learning', 'tutoring', 'LMS'],
  },
  'healthcare': {
    name: 'Healthcare',
    icon: '🏥',
    description: 'Pre-auth to discharge in one automated flow.',
    color: 'bg-teal-100 dark:bg-teal-900/20',
    tags: ['pre-auth', 'claims', 'discharge'],
  },
  'real-estate': {
    name: 'Real Estate',
    icon: '🏠',
    description: 'Rent collection to tenant onboarding, fully automated.',
    color: 'bg-amber-100 dark:bg-amber-900/20',
    tags: ['rent', 'tenants', 'property'],
  },
  'marketing': {
    name: 'Marketing',
    icon: '📣',
    description: 'Content factory on demand — SEO, social, campaigns.',
    color: 'bg-fuchsia-100 dark:bg-fuchsia-900/20',
    tags: ['content', 'SEO', 'social media'],
  },
  'cybersecurity': {
    name: 'Cybersecurity',
    icon: '🔒',
    description: 'SIEM triage without analyst fatigue.',
    color: 'bg-gray-100 dark:bg-gray-900/20',
    tags: ['SIEM', 'threat detection', 'incident response'],
  },
  'logistics': {
    name: 'Logistics',
    icon: '🚚',
    description: 'Freight invoices audited instantly.',
    color: 'bg-lime-100 dark:bg-lime-900/20',
    tags: ['freight', 'invoices', 'tracking'],
  },
  'insurance': {
    name: 'Insurance',
    icon: '🛡️',
    description: 'FNOL to settlement in hours.',
    color: 'bg-violet-100 dark:bg-violet-900/20',
    tags: ['FNOL', 'claims', 'settlement'],
  },
  'customer-support': {
    name: 'Customer Support',
    icon: '💬',
    description: 'Tier-1 auto-resolved round the clock.',
    color: 'bg-sky-100 dark:bg-sky-900/20',
    tags: ['tickets', 'chat', 'escalation'],
  },
  'manufacturing': {
    name: 'Manufacturing',
    icon: '🔧',
    description: 'Predictive maintenance before downtime strikes.',
    color: 'bg-stone-100 dark:bg-stone-900/20',
    tags: ['maintenance', 'quality', 'production'],
  },
  'banking-fintech': {
    name: 'Banking & FinTech',
    icon: '🏦',
    description: 'KYC, AML, and fraud detection on one platform.',
    color: 'bg-blue-100 dark:bg-blue-900/20',
    tags: ['KYC', 'AML', 'fraud'],
  },
  'agriculture': {
    name: 'Agriculture',
    icon: '🌾',
    description: 'Mandi prices to crop advice, automated with AI.',
    color: 'bg-green-100 dark:bg-green-900/20',
    tags: ['crop', 'mandi', 'advisory'],
  },
  'hospitality-travel': {
    name: 'Hospitality & Travel',
    icon: '✈️',
    description: 'Dynamic pricing meets AI-powered operations.',
    color: 'bg-orange-100 dark:bg-orange-900/20',
    tags: ['pricing', 'bookings', 'hospitality'],
  },
  'media-publishing': {
    name: 'Media & Publishing',
    icon: '📰',
    description: 'Moderate, publish, and grow content at scale.',
    color: 'bg-purple-100 dark:bg-purple-900/20',
    tags: ['moderation', 'publishing', 'content'],
  },
  'pharmaceutical': {
    name: 'Pharmaceutical',
    icon: '💊',
    description: 'CDSCO filings without the compliance backlog.',
    color: 'bg-red-100 dark:bg-red-900/20',
    tags: ['CDSCO', 'filings', 'regulatory'],
  },
  'telecom': {
    name: 'Telecom',
    icon: '📡',
    description: 'Churn predicted, retained automatically.',
    color: 'bg-indigo-100 dark:bg-indigo-900/20',
    tags: ['churn', 'billing', 'network'],
  },
  'construction': {
    name: 'Construction',
    icon: '🏗️',
    description: 'RA bills and approvals on schedule.',
    color: 'bg-yellow-100 dark:bg-yellow-900/20',
    tags: ['RA bills', 'approvals', 'project'],
  },
  'energy-utilities': {
    name: 'Energy & Utilities',
    icon: '⚡',
    description: 'Smart meter anomalies caught before they escalate.',
    color: 'bg-amber-100 dark:bg-amber-900/20',
    tags: ['smart meter', 'anomalies', 'utilities'],
  },
  'accounting-ca': {
    name: 'Accounting / CA Firm',
    icon: '📊',
    description: 'ITR, TDS, GST for 50+ clients at once.',
    color: 'bg-emerald-100 dark:bg-emerald-900/20',
    tags: ['ITR', 'TDS', 'GST'],
  },
  'food-restaurant': {
    name: 'Food & Restaurant',
    icon: '🍽️',
    description: 'Reconcile Swiggy, Zomato, ONDC daily.',
    color: 'bg-rose-100 dark:bg-rose-900/20',
    tags: ['Swiggy', 'Zomato', 'ONDC'],
  },
  'recruitment': {
    name: 'Recruitment',
    icon: '🎯',
    description: 'Screen 200 resumes in 20 minutes.',
    color: 'bg-pink-100 dark:bg-pink-900/20',
    tags: ['resume screening', 'JD', 'interviews'],
  },
  'automobile': {
    name: 'Automobile & EV',
    icon: '🚗',
    description: 'RC transfer to service reminders, automated.',
    color: 'bg-slate-100 dark:bg-slate-900/20',
    tags: ['RC', 'service', 'EV'],
  },
  'nonprofit-ngo': {
    name: 'Non-Profit & NGO',
    icon: '🤝',
    description: 'FCRA, CSR, and donor operations automated.',
    color: 'bg-teal-100 dark:bg-teal-900/20',
    tags: ['FCRA', 'CSR', 'donors'],
  },
  'events-mice': {
    name: 'Events & MICE',
    icon: '🎪',
    description: 'Vendor RFQ to PO without spreadsheets.',
    color: 'bg-fuchsia-100 dark:bg-fuchsia-900/20',
    tags: ['RFQ', 'PO', 'events'],
  },
  'wealth-management': {
    name: 'Wealth Management',
    icon: '💎',
    description: 'Portfolio rebalancing and SEBI compliance filings.',
    color: 'bg-yellow-100 dark:bg-yellow-900/20',
    tags: ['portfolio', 'SEBI', 'rebalancing'],
  },
  'fashion-apparel': {
    name: 'Fashion & Apparel',
    icon: '👗',
    description: 'Markdown optimization and AI-powered inventory.',
    color: 'bg-pink-100 dark:bg-pink-900/20',
    tags: ['inventory', 'markdown', 'apparel'],
  },
  'architecture-interior': {
    name: 'Architecture & Design',
    icon: '🏛️',
    description: 'Drawing approvals tracked and managed automatically.',
    color: 'bg-stone-100 dark:bg-stone-900/20',
    tags: ['drawings', 'approvals', 'design'],
  },
  'public-health': {
    name: 'Public Health',
    icon: '🏨',
    description: 'PM-JAY claims and HMIS reporting automated.',
    color: 'bg-cyan-100 dark:bg-cyan-900/20',
    tags: ['PM-JAY', 'HMIS', 'claims'],
  },
};

// ── Deploy parameter modal ────────────────────────────────────────────────────

function DeployParamModal({
  template,
  onClose,
  onDeployed,
}: {
  template: MarketplaceV2Template;
  onClose: () => void;
  onDeployed: (agentId: string) => void;
}) {
  const paramDefs = Object.entries(template.parameters_schema?.properties ?? {}).map(
    ([name, def]) => ({
      name,
      type: def.type ?? 'string',
      description: def.description ?? '',
      enumValues: def.enum,
      defaultValue: def.default != null ? String(def.default) : '',
      required: (template.parameters_schema?.required ?? []).includes(name),
    }),
  );

  const [params, setParams] = useState<Record<string, string>>(
    Object.fromEntries(paramDefs.map((p) => [p.name, p.defaultValue])),
  );
  const [deploying, setDeploying] = useState(false);
  const [result, setResult] = useState<{ agent_id: string; agent_name?: string } | null>(null);

  const allRequiredFilled = paramDefs
    .filter((p) => p.required)
    .every((p) => (params[p.name] ?? '').trim().length > 0);

  const handleDeploy = async () => {
    setDeploying(true);
    try {
      const res = await marketplaceApi.deploy(template.template_id, params);
      if (res.agent_id) {
        setResult({ agent_id: res.agent_id, agent_name: res.agent_name });
        onDeployed(res.agent_id);
        toast({ kind: 'success', message: `Agent "${res.agent_name ?? res.agent_id}" deployed!` });
      } else {
        toast({ kind: 'error', message: res.error ?? 'Deploy failed' });
      }
    } catch (e) {
      toast({ kind: 'error', message: `Deploy failed: ${String(e)}` });
    } finally {
      setDeploying(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[300] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />
      <div className="relative bg-card border border-border rounded-xl shadow-2xl max-w-md w-full p-6 space-y-4 max-h-[85vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 min-w-0">
            <div className="p-1.5 bg-primary/10 rounded-lg shrink-0">
              <Package className="h-4 w-4 text-primary" aria-hidden="true" />
            </div>
            <div className="min-w-0">
              <h2 className="text-sm font-bold truncate">{template.name}</h2>
              <p className="text-[10px] text-muted-foreground">Configure &amp; deploy</p>
            </div>
          </div>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground p-1" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Success state */}
        {result ? (
          <div className="bg-green-50 dark:bg-green-950/30 border border-green-200 dark:border-green-800 rounded-xl p-4 space-y-2">
            <p className="text-sm font-semibold text-green-800 dark:text-green-300">Agent deployed!</p>
            <p className="text-xs text-green-700 dark:text-green-400 font-mono">{result.agent_id}</p>
            {result.agent_name && <p className="text-xs text-green-700 dark:text-green-400">{result.agent_name}</p>}
            <button onClick={onClose} className="text-xs text-green-700 dark:text-green-400 underline">Close</button>
          </div>
        ) : (
          <>
            {/* Parameters */}
            {paramDefs.length === 0 ? (
              <p className="text-sm text-muted-foreground">This agent has no required parameters.</p>
            ) : (
              <div className="space-y-3">
                {paramDefs.map((p) => (
                  <div key={p.name}>
                    <label className="block text-xs font-medium mb-1" htmlFor={`dp-${p.name}`}>
                      {p.name}
                      {p.required && <span className="text-red-500 ml-0.5">*</span>}
                      {p.description && (
                        <span className="font-normal text-muted-foreground ml-1">— {p.description}</span>
                      )}
                    </label>
                    {p.enumValues ? (
                      <select
                        id={`dp-${p.name}`}
                        value={params[p.name] ?? ''}
                        onChange={(e) => setParams((prev) => ({ ...prev, [p.name]: e.target.value }))}
                        className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary"
                      >
                        <option value="">Select…</option>
                        {p.enumValues.map((v) => <option key={v} value={v}>{v}</option>)}
                      </select>
                    ) : (
                      <input
                        id={`dp-${p.name}`}
                        type="text"
                        value={params[p.name] ?? ''}
                        onChange={(e) => setParams((prev) => ({ ...prev, [p.name]: e.target.value }))}
                        placeholder={p.defaultValue || `Enter ${p.name}…`}
                        className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary"
                      />
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* Required fields warning */}
            {paramDefs.some((p) => p.required) && !allRequiredFilled && (
              <p className="text-xs text-amber-600 dark:text-amber-400">
                Fill in required fields (<span className="text-red-500">*</span>) to deploy.
              </p>
            )}

            {/* Action buttons */}
            <div className="flex gap-3 pt-1">
              <button
                onClick={() => void handleDeploy()}
                disabled={deploying || (paramDefs.length > 0 && !allRequiredFilled)}
                className="flex-1 flex items-center justify-center gap-2 py-2.5 bg-primary text-primary-foreground text-sm font-medium rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
              >
                {deploying ? (
                  <><Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Deploying…</>
                ) : (
                  <><Zap className="h-4 w-4" aria-hidden="true" /> Deploy Agent</>
                )}
              </button>
              <button onClick={onClose} className="px-4 py-2.5 border border-input text-sm rounded-lg hover:bg-muted/50">
                Cancel
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ── Agent template card ───────────────────────────────────────────────────────

function DomainAgentCard({
  template,
  onDeploy,
  onConfigure,
  deploying,
  deployed,
}: {
  template: MarketplaceV2Template;
  onDeploy: () => void;
  onConfigure: () => void;
  deploying: boolean;
  deployed?: string;
}) {
  const requiredParams = template.parameters_schema?.required ?? [];
  const hasRequiredParams = requiredParams.length > 0;

  return (
    <div className="bg-card border border-border rounded-xl p-4 flex flex-col gap-3 hover:border-primary/30 hover:shadow-sm transition-[color,background-color,border-color,opacity,box-shadow,transform]">
      <div className="flex items-start gap-2">
        <div className="p-1.5 bg-primary/10 rounded-lg shrink-0">
          <Package className="h-4 w-4 text-primary" aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 flex-wrap">
            <h4 className="text-sm font-semibold text-foreground leading-tight truncate">
              {template.name}
            </h4>
            {template.is_verified && (
              <ShieldCheck
                className="h-3.5 w-3.5 text-blue-500 shrink-0"
                aria-label="Verified"
              />
            )}
          </div>
          {template.author_name && (
            <p className="text-[10px] text-muted-foreground">by {template.author_name}</p>
          )}
        </div>
        <span className="text-[10px] text-muted-foreground shrink-0 flex items-center gap-1">
          <Download className="h-3 w-3" aria-hidden="true" />
          {(template.install_count ?? 0).toLocaleString()}
        </span>
      </div>

      <p className="text-xs text-muted-foreground line-clamp-2">{template.description}</p>

      {(template.required_connectors ?? []).length > 0 && (
        <div className="flex flex-wrap gap-1">
          {(template.required_connectors ?? []).slice(0, 4).map((c) => (
            <span
              key={c}
              className="text-[10px] bg-muted text-muted-foreground px-1.5 py-0.5 rounded font-mono"
            >
              {c}
            </span>
          ))}
          {(template.required_connectors ?? []).length > 4 && (
            <span className="text-[10px] text-muted-foreground">
              +{(template.required_connectors ?? []).length - 4} more
            </span>
          )}
        </div>
      )}

      {deployed ? (
        <div className="text-xs text-green-600 dark:text-green-400 bg-green-50 dark:bg-green-950/30 border border-green-200 dark:border-green-800 rounded-lg px-3 py-2">
          Deployed ✓ — <span className="font-mono">{deployed.slice(0, 12)}…</span>
        </div>
      ) : hasRequiredParams ? (
        /* Template has required parameters → must configure before deploying */
        <button
          onClick={onConfigure}
          className="flex items-center justify-center gap-1.5 py-1.5 px-3 text-xs font-medium border border-primary text-primary rounded-lg hover:bg-primary/10 transition-colors"
          aria-label={`Configure and deploy ${template.name}`}
        >
          <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
          Configure &amp; Deploy
        </button>
      ) : (
        /* No required parameters → quick one-click deploy */
        <button
          onClick={onDeploy}
          disabled={deploying}
          className="flex items-center justify-center gap-1.5 py-1.5 px-3 text-xs font-medium bg-primary text-primary-foreground rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
          aria-label={`Deploy ${template.name}`}
        >
          {deploying ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <Zap className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          {deploying ? 'Deploying…' : 'Deploy'}
        </button>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function DomainDetailPage() {
  const { domain: domainKey = '' } = useParams<{ domain: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const meta = DOMAIN_META[domainKey] ?? {
    name: domainKey,
    icon: '🏢',
    description: '',
    color: 'bg-muted',
    tags: [] as string[],
  };

  const [deployingId, setDeployingId] = useState<string | null>(null);
  const [deployedMap, setDeployedMap] = useState<Record<string, string>>({});
  const [instantiatingTemplate, setInstantiatingTemplate] = useState<GoalTemplate | null>(null);
  const [configuringTemplate, setConfiguringTemplate] = useState<MarketplaceV2Template | null>(null);

  const { data: marketplaceData, isLoading: marketplaceLoading } = useQuery({
    queryKey: ['marketplace-domain', domainKey],
    queryFn: () => marketplaceApi.list({ domain: domainKey, page_size: 50 }),
    staleTime: 5 * 60 * 1000,
    enabled: domainKey.length > 0,
  });

  const { data: templateData, isLoading: templateLoading } = useQuery({
    queryKey: ['templates-domain', domainKey],
    queryFn: () => templatesApi.list(domainKey),
    staleTime: 5 * 60 * 1000,
    enabled: domainKey.length > 0,
  });

  const agentTemplates: MarketplaceV2Template[] = marketplaceData?.items ?? marketplaceData?.templates ?? [];
  const goalTemplates: GoalTemplate[] = templateData ?? [];
  const isEmpty = agentTemplates.length === 0 && goalTemplates.length === 0;
  const isLoading = marketplaceLoading || templateLoading;

  const handleDeploy = async (template: MarketplaceV2Template) => {
    setDeployingId(template.template_id);
    try {
      const result = await marketplaceApi.deploy(template.template_id, {});
      if (result.agent_id) {
        setDeployedMap((prev) => ({ ...prev, [template.template_id]: result.agent_id! }));
        toast({ kind: 'success', message: `Agent "${result.agent_name ?? result.agent_id}" deployed!` });
        void qc.invalidateQueries({ queryKey: ['agents'] });
      } else {
        toast({ kind: 'error', message: result.error ?? 'Deploy failed' });
      }
    } catch (e) {
      toast({ kind: 'error', message: `Deploy failed: ${String(e)}` });
    } finally {
      setDeployingId(null);
    }
  };

  const handleUseTemplate = (template: GoalTemplate) => {
    if (template.parameters && template.parameters.length > 0) {
      setInstantiatingTemplate(template);
    } else {
      navigate('/goals', { state: { prefillGoal: template.goal_text } });
    }
  };

  return (
    <JARVISPageShell>
    <JARVISStagger className="p-6 max-w-7xl mx-auto">
      {/* Back link + breadcrumb */}
      <div className="flex items-center gap-2 mb-6 text-sm text-muted-foreground">
        <Link
          to="/domains"
          className="flex items-center gap-1.5 hover:text-foreground transition-colors"
          aria-label="Back to Domains"
        >
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          Back to Domains
        </Link>
        <span aria-hidden="true">/</span>
        <span className="text-foreground font-medium">{meta.name}</span>
      </div>

      {/* Domain hero */}
      <div className="mb-8">
        <div className={`inline-flex p-4 rounded-2xl ${meta.color} mb-4`}>
          <span className="text-4xl" role="img" aria-label={meta.name}>
            {meta.icon}
          </span>
        </div>
        <h1 className="text-3xl font-bold text-[#00D4FF]">{meta.name}</h1>
        {meta.description && (
          <p className="text-muted-foreground mt-2 text-lg max-w-2xl">{meta.description}</p>
        )}
        {meta.tags.length > 0 && (
          <div className="flex flex-wrap gap-2 mt-3">
            {meta.tags.map((tag) => (
              <span
                key={tag}
                className="text-xs px-2.5 py-1 bg-muted text-muted-foreground rounded-full"
              >
                {tag}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Stats strip */}
      {!isLoading && (
        <div className="flex items-center gap-6 mb-8 pb-6 border-b border-border text-sm text-muted-foreground">
          <span>
            <strong className="text-foreground text-lg font-bold">{agentTemplates.length}</strong>{' '}
            agent template{agentTemplates.length !== 1 ? 's' : ''}
          </span>
          <span className="text-border" aria-hidden="true">|</span>
          <span>
            <strong className="text-foreground text-lg font-bold">{goalTemplates.length}</strong>{' '}
            goal template{goalTemplates.length !== 1 ? 's' : ''}
          </span>
        </div>
      )}

      {/* Loading skeleton */}
      {isLoading && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-44 rounded-xl" />
          ))}
        </div>
      )}

      {/* Empty state */}
      {!isLoading && isEmpty && (
        <EmptyState
          title={`No templates for ${meta.name} yet`}
          description="Be the first to create a goal template or agent for this domain."
          action={
            <button
              onClick={() => navigate('/templates')}
              className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground text-sm font-medium rounded-lg hover:opacity-90 transition-opacity"
            >
              <Zap className="h-4 w-4" aria-hidden="true" />
              Create Template
            </button>
          }
        />
      )}

      {/* Content: two-column grid */}
      {!isLoading && !isEmpty && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
          {/* Agent Templates */}
          {agentTemplates.length > 0 && (
            <section aria-label="Agent Templates">
              <div className="flex items-center gap-2 mb-4">
                <Package className="h-5 w-5 text-primary" aria-hidden="true" />
                <h2 className="text-lg font-semibold text-foreground">Agent Templates</h2>
                <span className="text-sm text-muted-foreground">({agentTemplates.length})</span>
              </div>
              <div className="grid grid-cols-1 gap-3">
                {agentTemplates.map((t) => (
                  <DomainAgentCard
                    key={t.template_id}
                    template={t}
                    onDeploy={() => void handleDeploy(t)}
                    onConfigure={() => setConfiguringTemplate(t)}
                    deploying={deployingId === t.template_id}
                    deployed={deployedMap[t.template_id]}
                  />
                ))}
              </div>
            </section>
          )}

          {/* Goal Templates */}
          {goalTemplates.length > 0 && (
            <section aria-label="Goal Templates">
              <div className="flex items-center gap-2 mb-4">
                <FileText className="h-5 w-5 text-primary" aria-hidden="true" />
                <h2 className="text-lg font-semibold text-foreground">Goal Templates</h2>
                <span className="text-sm text-muted-foreground">({goalTemplates.length})</span>
              </div>
              <div className="grid grid-cols-1 gap-3">
                {goalTemplates.map((t) => (
                  <TemplateCard
                    key={t.id}
                    template={t}
                    onUse={handleUseTemplate}
                    onEdit={() => undefined}
                    onDelete={() => undefined}
                    pickerMode={true}
                  />
                ))}
              </div>
            </section>
          )}
        </div>
      )}

      {instantiatingTemplate && (
        <TemplateInstantiator
          template={instantiatingTemplate}
          onClose={() => setInstantiatingTemplate(null)}
          onUseInGoal={(text) => {
            navigate('/goals', { state: { prefillGoal: text } });
            setInstantiatingTemplate(null);
          }}
        />
      )}

      {configuringTemplate && (
        <DeployParamModal
          template={configuringTemplate}
          onClose={() => setConfiguringTemplate(null)}
          onDeployed={(agentId) => {
            setDeployedMap((prev) => ({ ...prev, [configuringTemplate.template_id]: agentId }));
            void qc.invalidateQueries({ queryKey: ['agents'] });
            // Keep modal open to show success state; user closes it manually
          }}
        />
      )}
    </JARVISStagger>
    </JARVISPageShell>
  );
}
