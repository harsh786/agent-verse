import { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { marketplaceApi, templatesApi, type MarketplaceV2Template } from '@/lib/api/client';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';

const DOMAINS = [
  { key: 'hr-talent', name: 'HR & Talent', icon: '👥', tagline: 'Hire, onboard, and retain faster', color: 'bg-blue-100 dark:bg-blue-900/20' },
  { key: 'software', name: 'Software Engineering', icon: '💻', tagline: 'AI pair programmer for your whole team', color: 'bg-purple-100 dark:bg-purple-900/20' },
  { key: 'devops', name: 'DevOps & SRE', icon: '⚙️', tagline: 'Zero-touch incident response', color: 'bg-orange-100 dark:bg-orange-900/20' },
  { key: 'sales-crm', name: 'Sales & CRM', icon: '📈', tagline: '10x pipeline without 10x headcount', color: 'bg-green-100 dark:bg-green-900/20' },
  { key: 'operations', name: 'Operations', icon: '🏭', tagline: 'Optimize procurement and logistics', color: 'bg-yellow-100 dark:bg-yellow-900/20' },
  { key: 'legal', name: 'Legal', icon: '⚖️', tagline: 'Contract review at paralegal speed', color: 'bg-[#0F1826] dark:bg-[#0F1117]/20' },
  { key: 'gst-tax', name: 'GST & Tax', icon: '🧾', tagline: 'Automate every GSTR filing', color: 'bg-red-100 dark:bg-red-900/20' },
  { key: 'invoicing-finance', name: 'Invoicing & Finance', icon: '💰', tagline: '3-way match in seconds', color: 'bg-emerald-100 dark:bg-emerald-900/20' },
  { key: 'government-portal', name: 'Government Portal', icon: '🏛️', tagline: 'GeM, RTI, MSME on autopilot', color: 'bg-indigo-100 dark:bg-indigo-900/20' },
  { key: 'e-commerce', name: 'E-Commerce', icon: '🛒', tagline: 'Catalog, orders, and reviews automated', color: 'bg-pink-100 dark:bg-pink-900/20' },
  { key: 'education', name: 'Education', icon: '🎓', tagline: 'Personalized learning at scale', color: 'bg-cyan-100 dark:bg-cyan-900/20' },
  { key: 'healthcare', name: 'Healthcare', icon: '🏥', tagline: 'Pre-auth to discharge in one flow', color: 'bg-teal-100 dark:bg-teal-900/20' },
  { key: 'real-estate', name: 'Real Estate', icon: '🏠', tagline: 'Rent collection to tenant onboarding', color: 'bg-amber-100 dark:bg-amber-900/20' },
  { key: 'marketing', name: 'Marketing', icon: '📣', tagline: 'Content factory on demand', color: 'bg-fuchsia-100 dark:bg-fuchsia-900/20' },
  { key: 'cybersecurity', name: 'Cybersecurity', icon: '🔒', tagline: 'SIEM triage without analyst fatigue', color: 'bg-[#0F1826] dark:bg-[#0F1117]/20' },
  { key: 'logistics', name: 'Logistics', icon: '🚚', tagline: 'Freight invoices audited instantly', color: 'bg-lime-100 dark:bg-lime-900/20' },
  { key: 'insurance', name: 'Insurance', icon: '🛡️', tagline: 'FNOL to settlement in hours', color: 'bg-violet-100 dark:bg-violet-900/20' },
  { key: 'customer-support', name: 'Customer Support', icon: '💬', tagline: 'Tier-1 auto-resolved round the clock', color: 'bg-sky-100 dark:bg-sky-900/20' },
  { key: 'manufacturing', name: 'Manufacturing', icon: '🔧', tagline: 'Predictive maintenance before downtime', color: 'bg-stone-100 dark:bg-stone-900/20' },
  { key: 'banking-fintech', name: 'Banking & FinTech', icon: '🏦', tagline: 'KYC, AML, and fraud on one platform', color: 'bg-blue-100 dark:bg-blue-900/20' },
  { key: 'agriculture', name: 'Agriculture', icon: '🌾', tagline: 'Mandi prices to crop advice, automated', color: 'bg-green-100 dark:bg-green-900/20' },
  { key: 'hospitality-travel', name: 'Hospitality & Travel', icon: '✈️', tagline: 'Dynamic pricing meets AI operations', color: 'bg-orange-100 dark:bg-orange-900/20' },
  { key: 'media-publishing', name: 'Media & Publishing', icon: '📰', tagline: 'Moderate, publish, and grow at scale', color: 'bg-purple-100 dark:bg-purple-900/20' },
  { key: 'pharmaceutical', name: 'Pharmaceutical', icon: '💊', tagline: 'CDSCO filings without the backlog', color: 'bg-red-100 dark:bg-red-900/20' },
  { key: 'telecom', name: 'Telecom', icon: '📡', tagline: 'Churn predicted, retained automatically', color: 'bg-indigo-100 dark:bg-indigo-900/20' },
  { key: 'construction', name: 'Construction', icon: '🏗️', tagline: 'RA bills and approvals on schedule', color: 'bg-yellow-100 dark:bg-yellow-900/20' },
  { key: 'energy-utilities', name: 'Energy & Utilities', icon: '⚡', tagline: 'Smart meter anomalies caught early', color: 'bg-amber-100 dark:bg-amber-900/20' },
  { key: 'accounting-ca', name: 'Accounting / CA Firm', icon: '📊', tagline: 'ITR, TDS, GST for 50+ clients at once', color: 'bg-emerald-100 dark:bg-emerald-900/20' },
  { key: 'food-restaurant', name: 'Food & Restaurant', icon: '🍽️', tagline: 'Reconcile Swiggy, Zomato, ONDC daily', color: 'bg-rose-100 dark:bg-rose-900/20' },
  { key: 'recruitment', name: 'Recruitment', icon: '🎯', tagline: 'Screen 200 resumes in 20 minutes', color: 'bg-pink-100 dark:bg-pink-900/20' },
  { key: 'automobile', name: 'Automobile & EV', icon: '🚗', tagline: 'RC transfer to service reminders', color: 'bg-[#0F1826] dark:bg-[#0F1117]/20' },
  { key: 'nonprofit-ngo', name: 'Non-Profit & NGO', icon: '🤝', tagline: 'FCRA, CSR, and donor ops automated', color: 'bg-teal-100 dark:bg-teal-900/20' },
  { key: 'events-mice', name: 'Events & MICE', icon: '🎪', tagline: 'Vendor RFQ to PO without spreadsheets', color: 'bg-fuchsia-100 dark:bg-fuchsia-900/20' },
  { key: 'wealth-management', name: 'Wealth Management', icon: '💎', tagline: 'Portfolio rebalancing and SEBI filings', color: 'bg-yellow-100 dark:bg-yellow-900/20' },
  { key: 'fashion-apparel', name: 'Fashion & Apparel', icon: '👗', tagline: 'Markdown optimization meets AI', color: 'bg-pink-100 dark:bg-pink-900/20' },
  { key: 'architecture-interior', name: 'Architecture & Design', icon: '🏛️', tagline: 'Drawing approvals tracked automatically', color: 'bg-stone-100 dark:bg-stone-900/20' },
  { key: 'public-health', name: 'Public Health', icon: '🏨', tagline: 'PM-JAY claims and HMIS reporting', color: 'bg-cyan-100 dark:bg-cyan-900/20' },
] as const;

type Domain = (typeof DOMAINS)[number];

/**
 * Normalise a domain key so that "e-commerce" and "ecommerce" both map to the
 * same bucket when counting marketplace / template data.
 */
const normalizeDomainKey = (key: string): string =>
  key.toLowerCase().replace(/[-_\s]/g, '');

function DomainCard({
  domain,
  counts,
  onClick,
}: {
  domain: Domain;
  counts?: { agents: number; templates: number };
  onClick: () => void;
}) {
  const hasData = counts && (counts.agents > 0 || counts.templates > 0);
  return (
    <div
      onClick={onClick}
      className="group cursor-pointer rounded-xl border bg-card hover:border-primary/50 hover:shadow-md transition-[color,background-color,border-color,opacity,box-shadow,transform] duration-200 overflow-hidden hover:-translate-y-0.5 transition-[transform,box-shadow] hover:shadow-[0_0_24px_rgba(0,212,255,0.20)]"
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && onClick()}
      aria-label={`Explore ${domain.name}`}
    >
      <div className={`p-4 ${domain.color}`}>
        <span className="text-3xl" role="img" aria-label={domain.name}>{domain.icon}</span>
      </div>
      <div className="p-4">
        <h3 className="font-semibold text-foreground">{domain.name}</h3>
        <p className="text-xs text-muted-foreground mt-1 leading-relaxed">{domain.tagline}</p>
        {hasData ? (
          <div className="flex gap-3 mt-3 text-xs text-muted-foreground">
            <span>{counts.agents} agent{counts.agents !== 1 ? 's' : ''}</span>
            <span>•</span>
            <span>{counts.templates} template{counts.templates !== 1 ? 's' : ''}</span>
          </div>
        ) : (
          <div className="mt-3 text-xs text-primary font-medium group-hover:underline">
            Explore →
          </div>
        )}
      </div>
    </div>
  );
}

export default function DomainsPage() {
  const navigate = useNavigate();
  const [search, setSearch] = useState('');

  // Fetch all marketplace templates to aggregate per-domain agent counts
  const { data: marketplaceData } = useQuery({
    queryKey: ['marketplace-all'],
    queryFn: () => marketplaceApi.list({}),
    staleTime: 5 * 60 * 1000,
  });

  // Fetch all goal templates to aggregate per-domain template counts
  const { data: templateData } = useQuery({
    queryKey: ['templates-all'],
    queryFn: () => templatesApi.list(),
    staleTime: 5 * 60 * 1000,
  });

  /**
   * Build a map of normalised-domain-key → { agents, templates }.
   * Both the marketplace and templates APIs may use slightly different key
   * spellings (e.g. "ecommerce" vs "e-commerce"), so we normalise before
   * counting and look up by the normalised form of each card's key.
   */
  const domainCounts = useMemo(() => {
    const counts: Record<string, { agents: number; templates: number }> = {};

    const agentItems: MarketplaceV2Template[] = marketplaceData?.items ?? marketplaceData?.templates ?? [];
    agentItems.forEach((t) => {
      const dk = normalizeDomainKey(t.domain ?? 'general');
      if (!counts[dk]) counts[dk] = { agents: 0, templates: 0 };
      counts[dk].agents++;
    });

    const templateItems = templateData ?? [];
    templateItems.forEach((t) => {
      const dk = normalizeDomainKey(t.domain ?? 'general');
      if (!counts[dk]) counts[dk] = { agents: 0, templates: 0 };
      counts[dk].templates++;
    });

    return counts;
  }, [marketplaceData, templateData]);

  const filtered = DOMAINS.filter(
    (d) =>
      !search ||
      d.name.toLowerCase().includes(search.toLowerCase()) ||
      d.tagline.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <JARVISPageShell>

      {/* a11y: live region for async updates */}
      <div aria-live="polite" aria-atomic="true" className="sr-only" />
    <JARVISStagger className="p-6 max-w-7xl mx-auto">
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-[#00D4FF]">Domain Solutions</h1>
        <p className="text-muted-foreground mt-2 text-lg">
          37 industry verticals, 200+ pre-built agents, 150+ goal templates — ready to install.
        </p>
      </div>

      <div className="mb-6">
        <input
          type="search"
          placeholder="Search domains…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full max-w-md px-4 py-2 rounded-lg border bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/30"
          aria-label="Search domains"
        />
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
        {filtered.map((d) => (
          <DomainCard
            key={d.key}
            domain={d}
            counts={domainCounts[normalizeDomainKey(d.key)]}
            onClick={() => navigate(`/domains/${d.key}`)}
          />
        ))}
      </div>

      {filtered.length === 0 && (
        <div className="text-center py-16 text-muted-foreground">
          No domains match &quot;{search}&quot;
        </div>
      )}
    </JARVISStagger>
    </JARVISPageShell>
  );
}
