import { NavLink, useNavigate } from "react-router-dom";
import { clsx } from "clsx";
import { useState } from "react";

import {
  LayoutDashboard, Target, Bot, Plug, Calendar, BookOpen, Database,
  Shield, ShieldCheck, Users, Activity, BarChart3, ShoppingBag, Building,
  Settings, ChevronLeft, ChevronDown, ChevronRight, Zap, CheckSquare, DollarSign,
  GitBranch, FlaskConical, BarChart2, Globe,
  Brain, FileBox, Wrench, Webhook, GraduationCap, Eye, Network,
  Bell, KeyRound, FileLock, X, Package, Ghost, TrendingUp, LayoutGrid,
  Hammer, Sparkles, Plus, Search, TestTube2, Microscope, MousePointer2, Hash,
  Library, ClipboardList, User, LogOut,
} from "lucide-react";
import { useUiStore } from "@/stores/ui";
import { useQuery } from "@tanstack/react-query";
import { governanceApi } from "@/lib/api/client";
import { useAuthStore } from "@/stores/auth";
import { useTranslation } from "react-i18next";

interface NavItem {
  to: string;
  icon: React.ElementType;
  label: string;
  badge?: number;
}

interface NavSection {
  heading: string;
  items: NavItem[];
}

// Enterprise items shown when the section is collapsed
const ENTERPRISE_PINNED = new Set([
  "/builder",
  "/marketplace",
  "/observability",
  "/playground",
  "/workflow-builder",
]);

export function Sidebar({ id }: { id?: string }) {
  const { sidebarOpen, toggleSidebar } = useUiStore();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const tenantId = useAuthStore((s) => s.tenantId);
  const plan = useAuthStore((s) => s.plan);
  const logout = useAuthStore((s) => s.logout);
  const { t } = useTranslation();
  const navigate = useNavigate();

  const [sidebarSearch, setSidebarSearch] = useState("");
  const [enterpriseExpanded, setEnterpriseExpanded] = useState(false);

  // Poll pending approvals every 10s for badge count
  const { data: approvals = [] } = useQuery({
    queryKey: ["approvals"],
    queryFn: () => governanceApi.listApprovals(),
    refetchInterval: 10_000,
    enabled: isAuthenticated,
  });
  const pendingCount = approvals.filter((a) => a.status === "pending").length;

  const NAV_SECTIONS: NavSection[] = [
    {
      heading: "Core",
      items: [
        { to: "/dashboard", icon: LayoutDashboard, label: "Dashboard" },
        { to: "/org",       icon: Building,        label: "Organizations" },
        { to: "/goals",     icon: Target,          label: t('nav.goals')  },
        { to: "/agents",    icon: Bot,             label: t('nav.agents') },
      ],
    },
    {
      heading: "Platform",
      items: [
        { to: "/connectors",    icon: Plug,         label: "Connectors"    },
        { to: "/knowledge",     icon: BookOpen,     label: t('nav.knowledge')  },
        { to: "/sources",       icon: Database,     label: "Sources"             },
        { to: "/knowledge-graph", icon: Network,     label: "Knowledge Graph"   },
        { to: "/schedules",     icon: Calendar,     label: "Schedules"         },
        { to: "/skills",        icon: Sparkles,     label: "Skills"        },
        { to: "/models",        icon: Brain,        label: "Model Registry" },
        { to: "/collaboration", icon: Users,        label: "Collaboration" },
        { to: "/coordination",  icon: Hash,         label: "Coordination" },
      ],
    },
    {
      heading: "Governance",
      items: [
        { to: "/governance",          icon: Shield,      label: "Governance"     },
        { to: "/approvals",           icon: CheckSquare, label: "Approvals",     badge: pendingCount > 0 ? pendingCount : undefined },
        { to: "/notifications",       icon: Bell,        label: "Notifications"  },
        { to: "/rbac",                icon: KeyRound,    label: "Access Control" },
        { to: "/compliance",          icon: FileLock,    label: "Compliance"     },
        { to: "/audit",               icon: ClipboardList, label: "Audit Log"    },
        { to: "/settings/guardrails", icon: ShieldCheck, label: "Guardrails"     },
        { to: "/settings/scopes",     icon: Hash,        label: "Scope Explorer" },
        { to: "/settings",            icon: Settings,    label: "Settings"       },
      ],
    },
    {
      heading: "Enterprise",
      items: [
        { to: "/builder",             icon: Hammer,       label: "AI Builder"       },
        { to: "/marketplace",         icon: ShoppingBag,  label: t('nav.marketplace') },
        { to: "/domains",             icon: LayoutGrid,   label: "Domains"          },
        { to: "/observability",       icon: Activity,     label: "Observability"    },
        { to: "/observability/cost",  icon: DollarSign,   label: "Cost Dashboard"   },
        { to: "/eval",                icon: BarChart3,    label: "Eval"             },
        { to: "/enterprise",          icon: Building,     label: "Enterprise"       },
        { to: "/analytics",           icon: BarChart2,    label: "Analytics"        },
        { to: "/workflow-builder",    icon: GitBranch,    label: "Workflow Builder" },
        { to: "/playground",          icon: FlaskConical, label: "Playground"       },
        { to: "/civilization",        icon: Globe,        label: "Civilization"     },
        { to: "/templates",           icon: Library,      label: "Templates"        },
        { to: "/goals/ghost-run",     icon: Ghost,        label: "Ghost Run"        },
        { to: "/self-improvement",    icon: TrendingUp,   label: "Self-Improvement" },
        { to: "/lab",                 icon: TestTube2,    label: "Agent Lab"        },
      ],
    },
    {
      heading: "Tooling",
      items: [
        { to: "/tools",              icon: Wrench,        label: "Tools"             },
        { to: "/memory",             icon: Brain,         label: "Memory"            },
        { to: "/artifacts",          icon: FileBox,       label: "Artifacts"         },
        { to: "/integrations",       icon: Webhook,       label: "Integrations"      },
        { to: "/perception",         icon: Eye,           label: "Perception"        },
        { to: "/training-export",    icon: GraduationCap, label: "Training Export"   },
        { to: "/a2a",                icon: Network,       label: "A2A"               },
        { to: "/rpa/live",           icon: MousePointer2, label: "RPA Sessions"      },
        { to: "/connectors/catalog", icon: Package,       label: "Connector Catalog" },
        { to: "/simulation",         icon: Microscope,    label: "Simulation"        },
        { to: "/settings/budgets",   icon: DollarSign,    label: "Budget Manager"    },
      ],
    },
  ];

  // Apply search filter when sidebar is open and search is non-empty
  const searchQuery = sidebarSearch.trim().toLowerCase();
  const filteredSections: NavSection[] = searchQuery
    ? NAV_SECTIONS.map((section) => ({
        ...section,
        items: section.items.filter((item) =>
          item.label.toLowerCase().includes(searchQuery)
        ),
      })).filter((section) => section.items.length > 0)
    : NAV_SECTIONS;

  return (
    <>
      {/* Mobile backdrop — click outside to close sidebar */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/40 z-[29] md:hidden"
          onClick={toggleSidebar}
          aria-hidden="true"
        />
      )}
      <aside
        id={id}
        className={clsx(
          // JARVIS surface — glass-dark with scanline depth
          "fixed inset-y-0 left-0 z-30 flex flex-col",
          "bg-[#0F1117] border-r border-[#1E2535]",
          // web-guidelines: no transition:all — list specific properties
          "transition-[width,transform] duration-200",
          sidebarOpen ? "w-64" : "w-16",
          sidebarOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"
        )}
      >
      {/* Mobile close button — only visible when open on mobile */}
      <button
        className="md:hidden absolute top-4 right-4 text-muted-foreground hover:text-foreground"
        onClick={toggleSidebar}
        aria-label="Close sidebar"
      >
        <X className="h-4 w-4" />
      </button>

      {/* Logo — JARVIS identity mark */}
      <div className="flex items-center gap-2.5 px-4 py-4 border-b border-[#1E2535]">
        <div className="relative flex-shrink-0">
          <Zap className="h-5 w-5 text-blue-400" aria-hidden />
          <span className="absolute -top-0.5 -right-0.5 h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse-glow" aria-hidden />
        </div>
        {sidebarOpen && (
          <span className="font-semibold text-[15px] text-[#F1F5F9] tracking-[-0.01em] truncate">
            AgentVerse
          </span>
        )}
      </div>

      {/* New Goal quick-action button */}
      <div className="px-3 pt-3 pb-1">
        <button
          onClick={() => navigate('/goals')}
          // web-guidelines: touch-action prevents double-tap zoom
          style={{ touchAction: 'manipulation' }}
          className={clsx(
            "flex items-center gap-2 px-3 py-2.5 rounded-lg w-full",
            "bg-blue-600 hover:bg-blue-500 active:scale-[0.98]",
            "text-white text-sm font-medium",
            // web-guidelines: transition specific props, not all
            "transition-[background-color,transform] duration-150",
            !sidebarOpen && "justify-center px-0"
          )}
          aria-label="Create new goal"
          title="New Goal"
        >
          <Plus className="h-4 w-4 flex-shrink-0" />
          {sidebarOpen && <span>New Goal</span>}
        </button>
      </div>

      {/* Sidebar search — visible only when expanded */}
      {sidebarOpen && (
        <div className="px-3 pb-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none" />
            <input
              value={sidebarSearch}
              onChange={(e) => setSidebarSearch(e.target.value)}
              placeholder="Search..."
              className="w-full pl-8 pr-3 py-1.5 text-xs border border-input rounded-md bg-background focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
        </div>
      )}

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-1" aria-label="Main navigation">
        {filteredSections.map(({ heading, items }) => {
          const isEnterprise = heading === "Enterprise";

          // For Enterprise section apply collapse logic (skip when searching)
          const visibleItems =
            isEnterprise && !enterpriseExpanded && !searchQuery
              ? items.filter((item) => ENTERPRISE_PINNED.has(item.to))
              : items;

          return (
            <div key={heading} className="mb-1">
              {/* Section heading — with collapse toggle for Enterprise */}
              {sidebarOpen ? (
                <div
                  className={clsx(
                    "flex items-center justify-between px-4 pt-3 pb-1",
                    isEnterprise && "cursor-pointer hover:text-foreground"
                  )}
                  onClick={
                    isEnterprise
                      ? () => setEnterpriseExpanded((v) => !v)
                      : undefined
                  }
                  onKeyDown={
                    isEnterprise
                      ? (e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            setEnterpriseExpanded((v) => !v);
                          }
                        }
                      : undefined
                  }
                  tabIndex={isEnterprise ? 0 : undefined}
                  role={isEnterprise ? "button" : undefined}
                  aria-expanded={isEnterprise ? enterpriseExpanded : undefined}
                >
                  <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground select-none">
                    {heading}
                  </p>
                  {isEnterprise && (
                    <span className="text-muted-foreground">
                      {enterpriseExpanded ? (
                        <ChevronDown className="h-3.5 w-3.5" />
                      ) : (
                        <ChevronRight className="h-3.5 w-3.5" />
                      )}
                    </span>
                  )}
                </div>
              ) : (
                <div className="mx-3 my-1 h-px bg-border" aria-hidden="true" />
              )}

              {visibleItems.map(({ to, icon: Icon, label, badge }) => (
                <div key={to} className="relative group">
                  <NavLink
                    to={to}
                    className={({ isActive }) =>
                      clsx(
                        "flex items-center gap-3 px-3 py-2.5 rounded-lg mx-1 text-sm font-medium",
                        // web-guidelines: list specific transition props
                        "transition-[background-color,color] duration-150",
                        // web-guidelines: touch-action
                        "select-none",
                        isActive
                          ? "bg-blue-500/10 text-blue-300 border-l-2 border-blue-500"
                          : "text-[#94A3B8] hover:bg-[#1A1F2E] hover:text-[#F1F5F9] border-l-2 border-transparent"
                      )
                    }
                    style={{ touchAction: 'manipulation' }}
                  >
                    <div className="relative flex-shrink-0">
                      <Icon className="h-5 w-5" aria-hidden="true" />
                      {/* Badge on collapsed sidebar */}
                      {!sidebarOpen && badge != null && badge > 0 && (
                        <span className="absolute -top-1 -right-1 flex items-center justify-center w-4 h-4 rounded-full bg-orange-500 text-white text-xs font-bold leading-none">
                          {badge > 9 ? "9+" : badge}
                        </span>
                      )}
                    </div>
                    {sidebarOpen && (
                      <>
                        <span className="truncate flex-1">{label}</span>
                        {badge != null && badge > 0 && (
                          <span className="ml-auto flex items-center justify-center px-1.5 min-w-[1.25rem] h-5 rounded-full bg-orange-500 text-white text-xs font-bold">
                            {badge > 99 ? "99+" : badge}
                          </span>
                        )}
                      </>
                    )}
                  </NavLink>
                  {/* Tooltip when collapsed */}
                  {!sidebarOpen && (
                    <span className="absolute left-full ml-2 px-2 py-1 bg-popover border border-border text-popover-foreground text-xs rounded shadow-md opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap z-50 pointer-events-none top-1/2 -translate-y-1/2">
                      {label}
                    </span>
                  )}
                </div>
              ))}

              {/* "More / Less" toggle for Enterprise when not searching */}
              {isEnterprise && sidebarOpen && !searchQuery && (
                <button
                  onClick={() => setEnterpriseExpanded((v) => !v)}
                  className="w-full flex items-center gap-2 px-4 py-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
                >
                  {enterpriseExpanded ? (
                    <>
                      <ChevronDown className="h-3 w-3" />
                      <span>Less</span>
                    </>
                  ) : (
                    <>
                      <ChevronRight className="h-3 w-3" />
                      <span>More ({items.filter(i => !ENTERPRISE_PINNED.has(i.to)).length} hidden)</span>
                    </>
                  )}
                </button>
              )}
            </div>
          );
        })}
      </nav>

      {/* User profile section */}
      {sidebarOpen ? (
        <div className="px-3 py-3 border-t border-border">
          <div className="flex items-center gap-2">
            <div className="h-7 w-7 rounded-full bg-primary/20 flex items-center justify-center shrink-0">
              <User className="h-4 w-4 text-primary" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium truncate">{tenantId ? `${tenantId.slice(0, 12)}…` : '—'}</p>
              <p className="text-[10px] text-muted-foreground capitalize">{plan || 'free'} plan</p>
            </div>
            <button
              onClick={logout}
              className="p-1 text-muted-foreground hover:text-destructive transition-colors"
              title="Sign out"
            >
              <LogOut className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      ) : (
        <div className="px-2 pb-3 border-t border-border pt-3">
          <button
            onClick={logout}
            className="w-full p-2 flex items-center justify-center text-muted-foreground hover:text-destructive hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors group relative"
            title="Sign out"
            aria-label="Sign out"
          >
            <LogOut className="h-4 w-4" />
            <span className="absolute left-full ml-2 px-2 py-1 bg-popover border border-border rounded text-xs whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-50">
              Sign out
            </span>
          </button>
        </div>
      )}

      {/* Collapse toggle */}
      <button
        onClick={toggleSidebar}
        className="flex items-center justify-center p-4 border-t border-[#1E2535] hover:bg-[#1A1F2E] transition-colors"
        aria-label={sidebarOpen ? "Collapse sidebar" : "Expand sidebar"}
      >
        <ChevronLeft
          className={clsx(
            "h-5 w-5 text-muted-foreground transition-transform duration-200",
            !sidebarOpen && "rotate-180"
          )}
        />
      </button>
      </aside>
    </>
  );
}
