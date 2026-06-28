import { NavLink } from "react-router-dom";
import { clsx } from "clsx";
import {
  LayoutDashboard, Target, Bot, Plug, Calendar, BookOpen,
  Shield, Users, Activity, BarChart3, ShoppingBag, Building,
  Settings, ChevronLeft, Zap, CheckSquare, DollarSign,
  GitBranch, FlaskConical, BarChart2, Globe,
  Brain, FileBox, Wrench, Webhook, GraduationCap, Eye, Network,
  Bell, KeyRound, FileLock, X, Package, Ghost,
} from "lucide-react";
import { useUiStore } from "@/stores/ui";
import { useQuery } from "@tanstack/react-query";
import { governanceApi } from "@/lib/api/client";
import { useAuthStore } from "@/stores/auth";

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

export function Sidebar() {
  const { sidebarOpen, toggleSidebar } = useUiStore();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

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
        { to: "/goals",     icon: Target,          label: "Goals"     },
        { to: "/agents",    icon: Bot,             label: "Agents"    },
      ],
    },
    {
      heading: "Platform",
      items: [
        { to: "/connectors",    icon: Plug,         label: "Connectors"    },
        { to: "/knowledge",     icon: BookOpen,     label: "Knowledge"     },
        { to: "/schedules",     icon: Calendar,     label: "Schedules"     },
        { to: "/collaboration", icon: Users,        label: "Collaboration" },
      ],
    },
    {
      heading: "Governance",
      items: [
        { to: "/governance",    icon: Shield,      label: "Governance"     },
        { to: "/approvals",     icon: CheckSquare, label: "Approvals",     badge: pendingCount > 0 ? pendingCount : undefined },
        { to: "/notifications", icon: Bell,        label: "Notifications"  },
        { to: "/rbac",          icon: KeyRound,    label: "Access Control" },
        { to: "/compliance",    icon: FileLock,    label: "Compliance"     },
        { to: "/audit",         icon: Activity,    label: "Audit Log"      },
        { to: "/settings",      icon: Settings,    label: "Settings"       },
      ],
    },
    {
      heading: "Enterprise",
      items: [
        { to: "/marketplace",       icon: ShoppingBag,  label: "Marketplace"      },
        { to: "/observability",     icon: Activity,     label: "Observability"    },
        { to: "/observability/cost",icon: DollarSign,   label: "Cost Dashboard"   },
        { to: "/eval",              icon: BarChart3,    label: "Eval"             },
        { to: "/enterprise",        icon: Building,     label: "Enterprise"       },
        { to: "/analytics",         icon: BarChart2,    label: "Analytics"        },
        { to: "/workflow-builder",  icon: GitBranch,    label: "Workflow Builder" },
        { to: "/playground",        icon: FlaskConical, label: "Playground"       },
        { to: "/civilization",      icon: Globe,        label: "Civilization"     },
        { to: "/templates",         icon: BookOpen,     label: "Templates"        },
        { to: "/goals/ghost-run",   icon: Ghost,        label: "Ghost Run"        },
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
        { to: "/rpa/live",           icon: Activity,      label: "RPA Sessions"      },
        { to: "/connectors/catalog", icon: Package,       label: "Connector Catalog" },
        { to: "/simulation",         icon: FlaskConical,  label: "Simulation"        },
      ],
    },
  ];

  return (
    <aside
      className={clsx(
        "fixed inset-y-0 left-0 z-30 flex flex-col bg-card border-r border-border",
        "transition-all duration-200 ease-in-out",
        sidebarOpen ? "w-64" : "w-16",
        // On mobile: translate off-screen when closed, full overlay when open
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

      {/* Logo */}
      <div className="flex items-center gap-2 px-4 py-4 border-b border-border">
        <Zap className="h-6 w-6 text-primary flex-shrink-0" />
        {sidebarOpen && (
          <span className="font-bold text-lg truncate">AgentVerse</span>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-3" aria-label="Main navigation">
        {NAV_SECTIONS.map(({ heading, items }) => (
          <div key={heading} className="mb-1">
            {sidebarOpen && (
              <p className="px-4 pt-3 pb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground select-none">
                {heading}
              </p>
            )}
            {!sidebarOpen && (
              <div className="mx-3 my-1 h-px bg-border" aria-hidden="true" />
            )}
            {items.map(({ to, icon: Icon, label, badge }) => (
              <div key={to} className="relative group">
                <NavLink
                  to={to}
                  className={({ isActive }) =>
                    clsx(
                      "flex items-center gap-3 px-4 py-2.5 text-sm font-medium transition-colors",
                      "hover:bg-muted/60 focus-visible:bg-muted/60",
                      isActive
                        ? "bg-primary/10 text-primary font-medium border-l-2 border-primary"
                        : "text-muted-foreground border-l-2 border-transparent"
                    )
                  }
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
          </div>
        ))}
      </nav>

      {/* Collapse toggle */}
      <button
        onClick={toggleSidebar}
        className="flex items-center justify-center p-4 border-t border-border hover:bg-muted/60 transition-colors"
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
  );
}
