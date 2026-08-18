/**
 * MarketplacePage — world-class agent template marketplace.
 *
 * Wired to the V2 API:
 *   GET  /marketplace/templates   — paginated, searchable, domain-filtered
 *   POST /marketplace/templates/:id/deploy — atomic install (agent + install record)
 *   GET  /marketplace/templates/:id/reviews
 *   POST /marketplace/search
 *   POST /marketplace/publish (V1 — community submissions)
 *
 * Features:
 *  - Full-text search (debounced, server-side)
 *  - 10-domain filter bar
 *  - Rich template cards: star rating, install count, connector chips, verified badge
 *  - Detail drawer: long description, parameters form, reviews, deploy button
 *  - Publish modal: name, domain, goal_template, autonomy_mode, connectors
 */
import { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient, useInfiniteQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  ShoppingBag, Search, Star, Download, Plug, ShieldCheck,
  ChevronRight, X, Loader2, Plus, ExternalLink, Zap,
  Package, BookmarkPlus, Inbox,
} from "lucide-react";
import {
  marketplaceApi, templatesApi,
  type MarketplaceV2Template, type MarketplaceReview,
} from "@/lib/api/client";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { toast } from "@/stores/toast";
import { useAuthStore } from "@/stores/auth";

import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
// ── Constants ────────────────────────────────────────────────────────────────

const DOMAINS = [
  { key: "all",                label: "All"             },
  { key: "software",           label: "Software"        },
  { key: "devops",             label: "DevOps"          },
  { key: "testing",            label: "Testing"         },
  { key: "hr",                 label: "HR"              },
  { key: "sales",              label: "Sales"           },
  { key: "support",            label: "Support"         },
  { key: "legal",              label: "Legal"           },
  { key: "finance",            label: "Finance"         },
  { key: "healthcare",         label: "Healthcare"      },
  { key: "ecommerce",          label: "E-commerce"      },
  // Domain-gallery aligned verticals
  { key: "gst-tax",            label: "GST & Tax"       },
  { key: "hr-talent",          label: "HR & Talent"     },
  { key: "banking-fintech",    label: "Banking"         },
  { key: "operations",         label: "Operations"      },
  { key: "government-portal",  label: "Government"      },
  { key: "education",          label: "Education"       },
  { key: "real-estate",        label: "Real Estate"     },
  { key: "marketing",          label: "Marketing"       },
  { key: "cybersecurity",      label: "Cybersecurity"   },
  { key: "logistics",          label: "Logistics"       },
  { key: "insurance",          label: "Insurance"       },
  { key: "manufacturing",      label: "Manufacturing"   },
  { key: "accounting-ca",      label: "Accounting"      },
  { key: "sales-crm",          label: "Sales & CRM"     },
  { key: "recruitment",        label: "Recruitment"     },
];

const PAGE_SIZE = 20;

const DOMAIN_COLORS: Record<string, string> = {
  software:          "bg-violet-100 text-violet-800 dark:bg-violet-900/30 dark:text-violet-300",
  devops:            "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300",
  testing:           "bg-cyan-100 text-cyan-800 dark:bg-cyan-900/30 dark:text-cyan-300",
  hr:                "bg-pink-100 text-pink-800 dark:bg-pink-900/30 dark:text-pink-300",
  sales:             "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300",
  support:           "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300",
  legal:             "bg-indigo-100 text-indigo-800 dark:bg-indigo-900/30 dark:text-indigo-300",
  healthcare:        "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300",
  education:         "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300",
  finance:           "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300",
  ecommerce:         "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300",
  "e-commerce":      "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300",
  marketing:         "bg-rose-100 text-rose-800 dark:bg-rose-900/30 dark:text-rose-300",
  data:              "bg-sky-100 text-sky-800 dark:bg-sky-900/30 dark:text-sky-300",
  engineering:       "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300",
  operations:        "bg-teal-100 text-teal-800 dark:bg-teal-900/30 dark:text-teal-300",
  logistics:         "bg-lime-100 text-lime-800 dark:bg-lime-900/30 dark:text-lime-300",
  cybersecurity:     "bg-[#0F1826] text-slate-800 dark:bg-slate-900/30 dark:text-slate-300",
  insurance:         "bg-stone-100 text-stone-800 dark:bg-stone-900/30 dark:text-stone-300",
  manufacturing:     "bg-[#0F1826] text-zinc-800 dark:bg-zinc-900/30 dark:text-zinc-300",
  "real-estate":     "bg-amber-50 text-amber-900 dark:bg-amber-900/20 dark:text-amber-200",
  government:        "bg-neutral-100 text-neutral-800 dark:bg-neutral-900/30 dark:text-neutral-300",
  recruitment:       "bg-fuchsia-100 text-fuchsia-800 dark:bg-fuchsia-900/30 dark:text-fuchsia-300",
  accounting:        "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300",
  "accounting-ca":   "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300",
  banking:           "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300",
  "banking-fintech":  "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300",
  "gst-tax":         "bg-indigo-100 text-indigo-800 dark:bg-indigo-900/30 dark:text-indigo-300",
  "hr-talent":       "bg-pink-100 text-pink-800 dark:bg-pink-900/30 dark:text-pink-300",
  "sales-crm":       "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300",
  "government-portal": "bg-neutral-100 text-neutral-800 dark:bg-neutral-900/30 dark:text-neutral-300",
  general:           "bg-[#0F1826] text-[#A0B4CC] dark:bg-slate-800 dark:text-slate-300",
};

const AUTONOMY_LABELS: Record<string, string> = {
  "fully-autonomous":    "Fully Auto",
  "bounded-autonomous":  "Bounded",
  "supervised":          "Supervised",
  "manual":              "Manual",
};

// ── Star rating ───────────────────────────────────────────────────────────────

function StarRating({ value, max = 5, size = 12 }: { value: number; max?: number; size?: number }) {
  return (
    <span className="inline-flex items-center gap-0.5" aria-label={`${value.toFixed(1)} out of ${max} stars`}>
      {Array.from({ length: max }).map((_, i) => (
        <Star
          key={i}
          style={{ width: size, height: size }}
          className={i < Math.round(value) ? "text-amber-400 fill-amber-400" : "text-muted-foreground/30 fill-muted-foreground/10"}
          aria-hidden="true"
        />
      ))}
    </span>
  );
}

// ── Marketplace card ─────────────────────────────────────────────────────────

function MarketplaceCard({
  template,
  installed,
  onSelect,
  onDeploy,
  deploying,
}: {
  template: MarketplaceV2Template;
  installed?: boolean;
  onSelect: () => void;
  onDeploy: () => void;
  deploying: boolean;
}) {
  const { t } = useTranslation();
  const domainColor = DOMAIN_COLORS[template.domain] ?? "bg-muted text-muted-foreground";
  const hasParams = Object.keys(template.parameters_schema?.properties ?? {}).length > 0;

  return (
    <div
      className="bg-card border border-border rounded-xl p-5 flex flex-col gap-3 hover:border-primary/30 hover:shadow-md transition-[color,background-color,border-color,opacity,box-shadow,transform] cursor-pointer group"
      onClick={onSelect}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && onSelect()}
      aria-label={`View details for ${template.name}`}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <div className="p-2 bg-primary/10 rounded-lg shrink-0 group-hover:bg-primary/20 transition-colors">
            <Package className="h-4 w-4 text-primary" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 flex-wrap">
              <h3 className="text-sm font-semibold text-foreground leading-tight truncate">{template.name}</h3>
              {template.is_verified && (
                <ShieldCheck className="h-3.5 w-3.5 text-blue-500 shrink-0" aria-label="Verified" />
              )}
            </div>
            {template.author_name && (
              <p className="text-[10px] text-muted-foreground">by {template.author_name}</p>
            )}
          </div>
        </div>
        <span className={`shrink-0 text-[10px] px-2 py-0.5 rounded-full font-medium capitalize ${domainColor}`}>
          {template.domain}
        </span>
      </div>

      {/* Description */}
      <p className="text-xs text-muted-foreground line-clamp-2 flex-1">{template.description}</p>

      {/* Connectors */}
      {(template.required_connectors ?? []).length > 0 && (
        <div className="flex items-center gap-1 flex-wrap">
          <Plug className="h-3 w-3 text-muted-foreground shrink-0" aria-hidden="true" />
          {(template.required_connectors ?? []).slice(0, 4).map((c) => (
            <span key={c} className="text-[10px] bg-muted text-muted-foreground px-1.5 py-0.5 rounded font-mono">
              {c}
            </span>
          ))}
          {(template.required_connectors ?? []).length > 4 && (
            <span className="text-[10px] text-muted-foreground">+{(template.required_connectors ?? []).length - 4} more</span>
          )}
        </div>
      )}

      {/* Stats row */}
      <div className="flex items-center justify-between text-[10px] text-muted-foreground pt-1 border-t border-border">
        <div className="flex items-center gap-2">
          {(template.rating_avg ?? 0) > 0 && (
            <span className="flex items-center gap-1">
              <StarRating value={template.rating_avg ?? 0} size={10} />
              <span className="tabular-nums">{(template.rating_avg ?? 0).toFixed(1)}</span>
              {(template.rating_count ?? 0) > 0 && <span>({template.rating_count})</span>}
            </span>
          )}
          <span className="flex items-center gap-1">
            <Download className="h-3 w-3" aria-hidden="true" />
            {(template.install_count ?? 0).toLocaleString()} installs
          </span>
        </div>
        <span className="font-mono text-[10px]">{AUTONOMY_LABELS[template.autonomy_mode] ?? template.autonomy_mode}</span>
      </div>

      {/* Deploy / params CTA */}
      <div onClick={(e) => e.stopPropagation()}>
        {installed ? (
          <div className="px-3 py-2 bg-green-50 dark:bg-green-950/30 border border-green-200 dark:border-green-800 rounded-lg text-xs text-green-700 dark:text-green-300 flex items-center gap-1.5">
            <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
            Deployed ✓
          </div>
        ) : hasParams ? (
          <button
            onClick={onSelect}
            className="w-full flex items-center justify-center gap-1.5 py-1.5 px-3 text-xs font-medium border border-primary text-primary rounded-lg hover:bg-primary/10 transition-colors"
          >
            Configure &amp; Deploy
            <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        ) : (
          <button
            onClick={onDeploy}
            disabled={deploying}
            className="w-full py-1.5 px-3 text-xs font-medium bg-primary text-primary-foreground rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity flex items-center justify-center gap-1.5"
          >
            {deploying ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <Zap className="h-3.5 w-3.5" aria-hidden="true" />}
            {deploying ? "Deploying…" : t('marketplace.install')}
          </button>
        )}
      </div>
    </div>
  );
}

// ── Detail drawer ─────────────────────────────────────────────────────────────

function TemplateDetailDrawer({
  template,
  onClose,
  onDeployed,
}: {
  template: MarketplaceV2Template;
  onClose: () => void;
  onDeployed?: (id: string) => void;
}) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const domainColor = DOMAIN_COLORS[template.domain] ?? "bg-muted text-muted-foreground";

  // Build parameter inputs from JSON Schema
  const paramSchema = template.parameters_schema ?? {};
  const paramDefs = Object.entries(paramSchema.properties ?? {}).map(([name, def]) => ({
    name,
    type: def.type ?? "string",
    description: def.description ?? "",
    format: def.format,
    enumValues: def.enum,
    defaultValue: String(def.default ?? ""),
    required: (paramSchema.required ?? []).includes(name),
  }));

  const [params, setParams] = useState<Record<string, string>>(
    Object.fromEntries(paramDefs.map((p) => [p.name, p.defaultValue]))
  );
  const [deployResult, setDeployResult] = useState<{ agent_id: string; agent_name?: string } | null>(null);
  const [rating, setRating] = useState(0);
  const [reviewBody, setReviewBody] = useState("");

  const { data: reviews = [] } = useQuery<MarketplaceReview[]>({
    queryKey: ["marketplace-reviews", template.template_id],
    queryFn: () => marketplaceApi.getReviews(template.template_id),
    staleTime: 60_000,
  });

  const deployMutation = useMutation({
    mutationFn: () => marketplaceApi.deploy(template.template_id, params),
    onSuccess: (data) => {
      if (data.agent_id) {
        setDeployResult({ agent_id: data.agent_id, agent_name: data.agent_name });
        toast({ kind: "success", message: `Agent "${data.agent_name ?? data.agent_id}" deployed!` });
        onDeployed?.(template.template_id);
        qc.invalidateQueries({ queryKey: ["agents"] });
      } else {
        toast({ kind: "error", message: data.error ?? "Deploy failed" });
      }
    },
    onError: (e) => toast({ kind: "error", message: `Deploy failed: ${String(e)}` }),
  });

  const saveToLibraryMutation = useMutation({
    mutationFn: async () => {
      const goalText = template.template_config?.goal_template;
      if (!goalText) {
        throw new Error("This template has no goal pattern to save to the library.");
      }
      return templatesApi.create({
        name: template.name,
        description: template.description,
        goal_text: goalText,
        domain: template.domain,
      });
    },
    onSuccess: () => {
      toast({ kind: "success", message: "Saved to your Template Library!" });
      qc.invalidateQueries({ queryKey: ["templates"] });
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const reviewMutation = useMutation({
    mutationFn: () => marketplaceApi.addReview(template.template_id, { rating, body: reviewBody }),
    onSuccess: () => {
      toast({ kind: "success", message: "Review submitted!" });
      qc.invalidateQueries({ queryKey: ["marketplace-reviews", template.template_id] });
      setRating(0);
      setReviewBody("");
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const allRequiredFilled = paramDefs
    .filter((p) => p.required)
    .every((p) => (params[p.name] ?? "").trim().length > 0);

  return (
    <div className="fixed inset-0 z-[300] flex justify-end">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />
      <aside
        className="relative bg-card border-l border-border w-full max-w-lg flex flex-col shadow-2xl overflow-hidden"
        role="dialog"
        aria-label={`Details for ${template.name}`}
      >
        {/* Drawer header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <div className="p-1.5 bg-primary/10 rounded-lg shrink-0">
              <Package className="h-4 w-4 text-primary" aria-hidden="true" />
            </div>
            <div className="min-w-0">
              <h2 className="text-sm font-bold truncate">{template.name}</h2>
              <div className="flex items-center gap-2 mt-0.5">
                <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium capitalize ${domainColor}`}>
                  {template.domain}
                </span>
                {template.is_verified && (
                  <span className="flex items-center gap-1 text-[10px] text-blue-500">
                    <ShieldCheck className="h-3 w-3" aria-hidden="true" /> Verified
                  </span>
                )}
                <span className="text-[10px] text-muted-foreground">v{template.version}</span>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            <button
              onClick={() => saveToLibraryMutation.mutate()}
              disabled={saveToLibraryMutation.isPending}
              title="Save to Template Library"
              className="p-1.5 rounded-lg hover:bg-muted/60 text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
              aria-label="Save to Template Library"
            >
              <BookmarkPlus className="h-4 w-4" aria-hidden="true" />
            </button>
            <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-muted/60 text-muted-foreground hover:text-foreground transition-colors" aria-label="Close drawer">
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5">

          {/* Stats */}
          <div className="flex items-center gap-4 text-xs text-muted-foreground">
            {(template.rating_avg ?? 0) > 0 && (
              <span className="flex items-center gap-1.5">
                <StarRating value={template.rating_avg ?? 0} size={12} />
                <strong className="text-foreground">{(template.rating_avg ?? 0).toFixed(1)}</strong>
                <span>({template.rating_count ?? 0} reviews)</span>
              </span>
            )}
            <span className="flex items-center gap-1">
              <Download className="h-3.5 w-3.5" aria-hidden="true" />
              {(template.install_count ?? 0).toLocaleString()} installs
            </span>
            {template.author_name && (
              <span>by <strong className="text-foreground">{template.author_name}</strong></span>
            )}
          </div>

          {/* Description */}
          <div>
            <p className="text-sm text-muted-foreground leading-relaxed">
              {template.long_description ?? template.description}
            </p>
          </div>

          {/* Goal template preview */}
          {template.template_config?.goal_template && (
            <div>
              <p className="text-xs font-semibold text-foreground mb-1.5">Goal Template</p>
              <p className="text-xs font-mono bg-muted/60 rounded-lg px-3 py-2 text-muted-foreground leading-relaxed">
                {template.template_config.goal_template}
              </p>
            </div>
          )}

          {/* Connectors */}
          {((template.required_connectors ?? []).length > 0 || (template.optional_connectors ?? []).length > 0) && (
            <div className="space-y-2">
              <p className="text-xs font-semibold text-foreground">Connectors</p>
              {(template.required_connectors ?? []).length > 0 && (
                <div>
                  <p className="text-[10px] text-muted-foreground mb-1">Required</p>
                  <div className="flex flex-wrap gap-1.5">
                    {(template.required_connectors ?? []).map((c) => (
                      <span key={c} className="text-xs bg-destructive/10 text-destructive dark:text-red-400 px-2 py-0.5 rounded font-mono">
                        {c}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {(template.optional_connectors ?? []).length > 0 && (
                <div>
                  <p className="text-[10px] text-muted-foreground mb-1">Optional</p>
                  <div className="flex flex-wrap gap-1.5">
                    {(template.optional_connectors ?? []).map((c) => (
                      <span key={c} className="text-xs bg-muted text-muted-foreground px-2 py-0.5 rounded font-mono">
                        {c}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Parameters form */}
          {paramDefs.length > 0 && (
            <div className="space-y-3">
              <p className="text-xs font-semibold text-foreground">Parameters</p>
              {paramDefs.map((p) => (
                <div key={p.name}>
                  <label className="block text-xs font-medium mb-1" htmlFor={`param-${p.name}`}>
                    {p.name}
                    {p.required && <span className="text-red-500 ml-0.5">*</span>}
                    {p.description && <span className="font-normal text-muted-foreground ml-1">— {p.description}</span>}
                  </label>
                  {p.enumValues ? (
                    <select
                      id={`param-${p.name}`}
                      value={params[p.name] ?? ""}
                      onChange={(e) => setParams((prev) => ({ ...prev, [p.name]: e.target.value }))}
                      className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary"
                    >
                      <option value="">Select…</option>
                      {p.enumValues.map((v) => (
                        <option key={v} value={v}>{v}</option>
                      ))}
                    </select>
                  ) : (
                    <input
                      id={`param-${p.name}`}
                      type={p.format === "uri" ? "url" : "text"}
                      value={params[p.name] ?? ""}
                      onChange={(e) => setParams((prev) => ({ ...prev, [p.name]: e.target.value }))}
                      placeholder={p.defaultValue || `Enter ${p.name}…`}
                      className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary font-mono"
                    />
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Deploy result */}
          {deployResult && (
            <div className="bg-green-50 dark:bg-green-950/30 border border-green-200 dark:border-green-800 rounded-xl p-4 space-y-2">
              <p className="text-sm font-semibold text-green-800 dark:text-green-300">Agent deployed!</p>
              <p className="text-xs text-green-700 dark:text-green-400 font-mono">{deployResult.agent_id}</p>
              {deployResult.agent_name && <p className="text-xs text-green-700 dark:text-green-400">{deployResult.agent_name}</p>}
              <button
                onClick={() => navigate(`/agents/${deployResult.agent_id}`)}
                className="flex items-center gap-1.5 text-xs text-green-700 dark:text-green-400 hover:underline"
              >
                <ExternalLink className="h-3 w-3" aria-hidden="true" />
                View agent
              </button>
            </div>
          )}

          {/* Reviews */}
          <div className="space-y-2">
            <p className="text-xs font-semibold text-foreground">
              Reviews {reviews.length > 0 && <span className="text-muted-foreground font-normal">({reviews.length})</span>}
            </p>
            {reviews.length === 0 ? (
              <p className="text-xs text-muted-foreground italic">No reviews yet. Be the first!</p>
            ) : (
              <div className="space-y-2">
                {reviews.slice(0, 5).map((r, i) => (
                  <div key={i} className="bg-muted/40 rounded-lg p-3 space-y-1">
                    <div className="flex items-center justify-between">
                      <StarRating value={r.rating} size={11} />
                      <div className="flex items-center gap-1.5">
                        {r.verified_install && (
                          <span className="text-[10px] text-green-600 dark:text-green-400 flex items-center gap-0.5">
                            <ShieldCheck className="h-3 w-3" aria-hidden="true" /> verified
                          </span>
                        )}
                        {r.created_at && (
                          <span className="text-[10px] text-muted-foreground">
                            {new Date(r.created_at).toLocaleDateString()}
                          </span>
                        )}
                      </div>
                    </div>
                    {r.title && <p className="text-xs font-medium">{r.title}</p>}
                    {r.body && <p className="text-xs text-muted-foreground">{r.body}</p>}
                  </div>
                ))}
              </div>
            )}

            {/* Write a review */}
            <div className="border border-border rounded-lg p-3 space-y-2">
              <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wide">Leave a review</p>
              <div className="flex gap-1">
                {[1, 2, 3, 4, 5].map((n) => (
                  <button
                    key={n}
                    onClick={() => setRating(n)}
                    className="focus:outline-none"
                    aria-label={`Rate ${n} star${n > 1 ? "s" : ""}`}
                  >
                    <Star
                      className={`h-4 w-4 transition-colors ${n <= rating ? "text-amber-400 fill-amber-400" : "text-muted-foreground/30 hover:text-amber-300"}`}
                      aria-hidden="true"
                    />
                  </button>
                ))}
              </div>
              <textarea
                value={reviewBody}
                onChange={(e) => setReviewBody(e.target.value)}
                placeholder="Share your experience…"
                rows={2}
                className="w-full text-xs px-2 py-1.5 border border-input rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary resize-none"
              />
              <button
                onClick={() => reviewMutation.mutate()}
                disabled={rating === 0 || reviewMutation.isPending}
                className="text-xs px-3 py-1.5 bg-primary text-primary-foreground rounded-md hover:opacity-90 disabled:opacity-50"
              >
                {reviewMutation.isPending ? "Submitting…" : "Submit review"}
              </button>
            </div>
          </div>
        </div>

        {/* Sticky deploy footer */}
        {!deployResult && (
          <div className="shrink-0 border-t border-border px-5 py-4 bg-card space-y-2">
            {paramDefs.length > 0 && !allRequiredFilled && (
              <p className="text-xs text-amber-600 dark:text-amber-400">Fill in required parameters above to deploy.</p>
            )}
            <button
              onClick={() => deployMutation.mutate()}
              disabled={deployMutation.isPending || (paramDefs.length > 0 && !allRequiredFilled)}
              className="w-full flex items-center justify-center gap-2 py-2.5 bg-primary text-primary-foreground text-sm font-medium rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
            >
              {deployMutation.isPending ? (
                <><Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Deploying…</>
              ) : (
                <><Zap className="h-4 w-4" aria-hidden="true" /> Deploy Agent</>
              )}
            </button>
          </div>
        )}
      </aside>
    </div>
  );
}

// ── Publish modal ─────────────────────────────────────────────────────────────

function PublishModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({
    name: "", domain: "software", description: "",
    goal_template: "", autonomy_mode: "bounded-autonomous", connectors: "",
  });
  const [published, setPublished] = useState<{ template_id: string; name: string } | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      marketplaceApi.publish({
        ...form,
        connectors: form.connectors ? form.connectors.split(",").map((s) => s.trim()).filter(Boolean) : [],
      }),
    onSuccess: (data) => {
      setPublished(data);
      qc.invalidateQueries({ queryKey: ["marketplace"] });
    },
    onError: (e) => toast({ kind: "error", message: `Publish failed: ${String(e)}` }),
  });

  return (
    <div className="fixed inset-0 z-[300] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />
      <div className="relative bg-card border border-border rounded-xl shadow-2xl max-w-lg w-full p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold">Publish to Marketplace</h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>

        {published ? (
          <div className="space-y-3">
            <div className="bg-green-50 dark:bg-green-950/30 border border-green-200 dark:border-green-800 rounded-xl p-4">
              <p className="text-sm font-semibold text-green-800 dark:text-green-300 mb-1">Published!</p>
              <p className="text-xs text-green-700 dark:text-green-400">
                <strong>{published.name}</strong> is now in the marketplace as{" "}
                <code className="font-mono">{published.template_id}</code>
              </p>
            </div>
            <button onClick={onClose} className="w-full py-2 border border-input text-sm rounded-lg hover:bg-muted/50">Done</button>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2">
                <label className="block text-xs font-medium mb-1" htmlFor="pub-name">Name <span className="text-red-500">*</span></label>
                <input id="pub-name" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                  placeholder="My DevOps Agent" className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
              </div>
              <div>
                <label className="block text-xs font-medium mb-1" htmlFor="pub-domain">Domain</label>
                <select id="pub-domain" value={form.domain} onChange={(e) => setForm((f) => ({ ...f, domain: e.target.value }))}
                  className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary">
                  {DOMAINS.filter((d) => d.key !== "all").map((d) => (
                    <option key={d.key} value={d.key}>{d.label}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium mb-1" htmlFor="pub-autonomy">Autonomy</label>
                <select id="pub-autonomy" value={form.autonomy_mode} onChange={(e) => setForm((f) => ({ ...f, autonomy_mode: e.target.value }))}
                  className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary">
                  <option value="supervised">Supervised</option>
                  <option value="bounded-autonomous">Bounded Autonomous</option>
                  <option value="fully-autonomous">Fully Autonomous</option>
                </select>
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium mb-1" htmlFor="pub-desc">Description <span className="text-red-500">*</span></label>
              <textarea id="pub-desc" value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                placeholder="What does this agent do?" rows={2}
                className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary resize-none" />
            </div>
            <div>
              <label className="block text-xs font-medium mb-1" htmlFor="pub-goal">
                Goal Template <span className="text-red-500">*</span>
                <span className="font-normal text-muted-foreground ml-1">(use {`{{param}}`} for parameters)</span>
              </label>
              <textarea id="pub-goal" value={form.goal_template} onChange={(e) => setForm((f) => ({ ...f, goal_template: e.target.value }))}
                placeholder="Deploy {{service}} to {{environment}}" rows={2}
                className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary resize-none font-mono" />
            </div>
            <div>
              <label className="block text-xs font-medium mb-1" htmlFor="pub-conn">Connectors <span className="text-muted-foreground font-normal">(comma-separated)</span></label>
              <input id="pub-conn" value={form.connectors} onChange={(e) => setForm((f) => ({ ...f, connectors: e.target.value }))}
                placeholder="jira, github, slack" className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
            </div>
            <div className="flex gap-3">
              <button onClick={() => mutation.mutate()}
                disabled={!form.name || !form.description || !form.goal_template || mutation.isPending}
                className="flex-1 py-2.5 bg-primary text-primary-foreground text-sm font-medium rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity">
                {mutation.isPending ? "Publishing…" : "Publish"}
              </button>
              <button onClick={onClose} className="px-4 py-2.5 border border-input text-sm rounded-lg hover:bg-muted/50">Cancel</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function MarketplacePage() {
  const { t } = useTranslation();
  const [domain, setDomain] = useState("all");
  const [searchInput, setSearchInput] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [selectedTemplate, setSelectedTemplate] = useState<MarketplaceV2Template | null>(null);
  const [publishOpen, setPublishOpen] = useState(false);
  const [deployingId, setDeployingId] = useState<string | null>(null);
  const [lastDeployedId, setLastDeployedId] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<"install_count" | "rating" | "created_at">("install_count");

  const domainFilter = domain !== "all" ? domain : null;

  // Fix 3: localStorage-backed install persistence, scoped per tenant
  const INSTALLS_KEY = `av_marketplace_installs_${useAuthStore.getState().tenantId}`;
  const [installedIds, setInstalledIds] = useState<Set<string>>(() => {
    try {
      const stored = localStorage.getItem(INSTALLS_KEY);
      return new Set(stored ? (JSON.parse(stored) as string[]) : []);
    } catch {
      return new Set<string>();
    }
  });

  const markInstalled = (id: string) => {
    setInstalledIds((prev) => {
      const next = new Set(prev);
      next.add(id);
      try { localStorage.setItem(INSTALLS_KEY, JSON.stringify([...next])); } catch { /* ignore */ }
      return next;
    });
  };

  // Debounce search input (400 ms)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setDebouncedSearch(searchInput), 400);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [searchInput]);

  // Fix 1+2: infinite query that switches to semantic search for 3+ char queries
  const {
    data: infiniteData,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading,
    isError,
  } = useInfiniteQuery({
    queryKey: ["marketplace", domainFilter, debouncedSearch, sortBy],
    queryFn: async ({ pageParam }) => {
      if (debouncedSearch.length >= 3) {
        return marketplaceApi.search(debouncedSearch, domainFilter ?? undefined);
      }
      return marketplaceApi.list({
        domain: domainFilter ?? undefined,
        search: debouncedSearch || undefined,
        page: pageParam as number,
        page_size: PAGE_SIZE,
        sort_by: sortBy,
      });
    },
    getNextPageParam: (lastPage, pages) => {
      const lp = lastPage as { total?: number };
      const total = lp?.total ?? 0;
      const loaded = pages.length * PAGE_SIZE;
      return loaded < total ? pages.length + 1 : undefined;
    },
    initialPageParam: 1,
    staleTime: 60_000,
  });

  const templates = infiniteData?.pages.flatMap((p) => {
    const page = p as { items?: MarketplaceV2Template[]; templates?: MarketplaceV2Template[] };
    return page.items ?? page.templates ?? [];
  }) ?? [];
  const total = (infiniteData?.pages[0] as { total?: number } | undefined)?.total ?? 0;

  // Fix 1: IntersectionObserver sentinel for infinite scroll
  const sentinelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
    const obs = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting && hasNextPage && !isFetchingNextPage) {
        void fetchNextPage();
      }
    }, { threshold: 0.1 });
    obs.observe(el);
    return () => obs.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  const handleQuickDeploy = async (template: MarketplaceV2Template) => {
    setDeployingId(template.template_id);
    try {
      const result = await marketplaceApi.deploy(template.template_id, {});
      if (result.agent_id) {
        markInstalled(template.template_id);
        setLastDeployedId(result.agent_id);
        toast({ kind: "success", message: `Agent "${result.agent_name ?? result.agent_id}" deployed!` });
      } else {
        toast({ kind: "error", message: result.error ?? "Deploy failed" });
      }
    } catch (e) {
      toast({ kind: "error", message: `Deploy failed: ${String(e)}` });
    } finally {
      setDeployingId(null);
    }
  };

  return (
    <JARVISPageShell>
    <div className="space-y-6 max-w-6xl">
      {/* Page header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <ShoppingBag className="h-6 w-6 text-primary" aria-hidden="true" />
            Marketplace
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Deploy pre-built agent workflows for any domain — one click to production
          </p>
        </div>
        <button
          onClick={() => setPublishOpen(true)}
          className="flex items-center gap-2 px-4 py-2 border border-border text-sm font-medium rounded-lg hover:bg-muted/60 transition-colors shrink-0"
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
          Publish
        </button>
      </div>

      {/* Quick deploy success banner */}
      {lastDeployedId && (
        <div className="bg-green-50 dark:bg-green-950/30 border border-green-200 dark:border-green-800 rounded-xl p-3 flex items-center justify-between">
          <p className="text-sm text-green-800 dark:text-green-300 font-mono">{lastDeployedId}</p>
          <button onClick={() => setLastDeployedId(null)} className="text-xs text-green-600 hover:text-green-800" aria-label="Dismiss deploy banner">×</button>
        </div>
      )}

      {/* Search bar + sort controls */}
      <div className="flex gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" aria-hidden="true" />
          <input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder={t('marketplace.search')}
            className="w-full pl-10 pr-10 py-2.5 text-sm border border-input rounded-xl bg-background focus:outline-none focus:ring-2 focus:ring-primary"
            aria-label="Search marketplace"
          />
          {searchInput && (
            <button
              onClick={() => setSearchInput("")}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              aria-label="Clear search"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          )}
        </div>

        {/* Fix 6: Sort segmented control */}
        <div className="flex items-center rounded-xl border border-input overflow-hidden text-xs shrink-0" role="group" aria-label="Sort order">
          {(
            [
              { key: "install_count", label: "Popular" },
              { key: "rating",        label: "Top Rated" },
              { key: "created_at",    label: "Newest" },
            ] as const
          ).map((opt) => (
            <button
              key={opt.key}
              onClick={() => setSortBy(opt.key)}
              className={`px-3 py-2.5 transition-colors ${
                sortBy === opt.key
                  ? "bg-primary text-primary-foreground"
                  : "hover:bg-muted/60 text-muted-foreground"
              }`}
              aria-pressed={sortBy === opt.key}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* Domain filter */}
      <div className="flex gap-2 flex-wrap">
        {DOMAINS.map((d) => (
          <button
            key={d.key}
            onClick={() => setDomain(d.key)}
            className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${
              domain === d.key
                ? "bg-primary text-primary-foreground border-primary"
                : "border-border hover:bg-muted/60"
            }`}
          >
            {d.label}
          </button>
        ))}
      </div>

      {/* Results */}
      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 9 }).map((_, i) => <Skeleton key={i} className="h-52 rounded-xl" />)}
        </div>
      ) : isError ? (
        <EmptyState
          icon={<Inbox size={40} />}
          title="Could not load marketplace"
          description="Ensure the backend is running and the insights router is registered."
          variant="float"
        />
      ) : templates.length === 0 ? (
        <EmptyState
          title={debouncedSearch ? `No results for "${debouncedSearch}"` : "No templates found"}
          description={domain !== "all" ? `No templates in the "${domain}" domain yet.` : "The marketplace is empty."}
        />
      ) : (
        <>
          <p className="text-xs text-muted-foreground">
            {total} template{total !== 1 ? "s" : ""}
            {domain !== "all" && ` in "${domain}"`}
            {debouncedSearch && ` matching "${debouncedSearch}"`}
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {templates.map((tmpl) => (
              <MarketplaceCard
                key={tmpl.template_id}
                template={tmpl}
                installed={installedIds.has(tmpl.template_id)}
                onSelect={() => setSelectedTemplate(tmpl)}
                onDeploy={() => handleQuickDeploy(tmpl)}
                deploying={deployingId === tmpl.template_id}
              />
            ))}
          </div>
          {/* Fix 1: Infinite scroll sentinel */}
          <div ref={sentinelRef} className="h-4" />
          {isFetchingNextPage && (
            <div className="flex justify-center py-4">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-label="Loading more" />
            </div>
          )}
        </>
      )}

      {/* Detail drawer */}
      {selectedTemplate && (
        <TemplateDetailDrawer
          template={selectedTemplate}
          onClose={() => setSelectedTemplate(null)}
          onDeployed={markInstalled}
        />
      )}

      {/* Publish modal */}
      {publishOpen && <PublishModal onClose={() => setPublishOpen(false)} />}
    </div>
    </JARVISPageShell>
  );
}
