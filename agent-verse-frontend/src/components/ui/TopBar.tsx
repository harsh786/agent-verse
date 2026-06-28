import { Search, Moon, Sun, LogOut, Menu } from "lucide-react";
import { useState, useRef, useCallback } from "react";
import { useUiStore } from "@/stores/ui";
import { useAuthStore } from "@/stores/auth";
import { useNavigate } from "react-router-dom";
import { PendingApprovalsBadge } from "@/components/ui/PendingApprovalsBadge";

interface SearchResult {
  type: "goal" | "agent" | "connector";
  id: string;
  label: string;
  sub?: string;
}

const TYPE_ICONS: Record<string, string> = {
  goal: "🎯",
  agent: "🤖",
  connector: "🔌",
};

const API_BASE = (import.meta as any).env?.VITE_API_URL ?? "http://localhost:8000";

export function TopBar() {
  const { theme, toggleTheme, toggleSidebar, openCommandPalette } = useUiStore();
  const { logout, plan, tenantId, apiKey } = useAuthStore();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [open, setOpen] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  function handleLogout() {
    logout();
    navigate("/auth");
  }

  const search = useCallback(
    async (q: string) => {
      if (!q.trim() || !apiKey) { setResults([]); return; }
      try {
        const [goalsRes, agentsRes, connRes] = await Promise.allSettled([
          fetch(`${API_BASE}/goals?limit=20`, { headers: { "X-API-Key": apiKey } }).then((r) => r.ok ? r.json() : { goals: [] }),
          fetch(`${API_BASE}/agents`, { headers: { "X-API-Key": apiKey } }).then((r) => r.ok ? r.json() : []),
          fetch(`${API_BASE}/connectors`, { headers: { "X-API-Key": apiKey } }).then((r) => r.ok ? r.json() : []),
        ]);

        const lower = q.toLowerCase();
        const matched: SearchResult[] = [];

        if (goalsRes.status === "fulfilled") {
          const goals = (goalsRes.value.goals ?? []) as Array<{ id: string; goal_id?: string; goal: string; status: string }>;
          goals.filter((g) => g.goal?.toLowerCase().includes(lower)).slice(0, 4).forEach((g) => {
            matched.push({ type: "goal", id: g.goal_id ?? g.id, label: g.goal.slice(0, 60), sub: g.status });
          });
        }
        if (agentsRes.status === "fulfilled") {
          const agents = agentsRes.value as Array<{ agent_id: string; name: string; autonomy_mode?: string }>;
          agents.filter((a) => a.name?.toLowerCase().includes(lower)).slice(0, 4).forEach((a) => {
            matched.push({ type: "agent", id: a.agent_id, label: a.name, sub: a.autonomy_mode });
          });
        }
        if (connRes.status === "fulfilled") {
          const conns = connRes.value as Array<{ server_id: string; name: string; status?: string }>;
          conns.filter((c) => c.name?.toLowerCase().includes(lower)).slice(0, 3).forEach((c) => {
            matched.push({ type: "connector", id: c.server_id, label: c.name, sub: c.status });
          });
        }

        setResults(matched);
      } catch {
        setResults([]);
      }
    },
    [apiKey]
  );

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const v = e.target.value;
    setQuery(v);
    setOpen(true);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => search(v), 300);
  }

  function handleSelect(r: SearchResult) {
    setOpen(false);
    setQuery("");
    setResults([]);
    if (r.type === "goal") navigate(`/goals/${r.id}`);
    else if (r.type === "agent") navigate(`/agents/${r.id}`);
    else navigate(`/connectors/${r.id}`);
  }

  return (
    <header className="flex items-center justify-between px-6 py-3 border-b border-border bg-card">
      {/* Mobile hamburger */}
      <button
        onClick={toggleSidebar}
        className="md:hidden p-2 rounded-md hover:bg-muted/60 text-muted-foreground hover:text-foreground transition-colors"
        aria-label="Toggle sidebar"
      >
        <Menu className="h-5 w-5" />
      </button>

      {/* Live search input */}
      <div className="relative">
        <div className="flex items-center gap-2 px-3 py-1.5 text-sm text-muted-foreground bg-muted rounded-md hover:bg-accent transition-colors min-w-[240px]">
          <Search className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
          <input
            type="search"
            placeholder="Search goals, agents, connectors…"
            value={query}
            onChange={handleChange}
            onFocus={() => setOpen(true)}
            onBlur={() => setTimeout(() => setOpen(false), 150)}
            aria-label="Global search"
            className="flex-1 bg-transparent outline-none text-foreground placeholder:text-muted-foreground text-sm min-w-[160px]"
          />
          {!query && (
            <button
              onClick={openCommandPalette}
              aria-label="Open command palette"
              className="hidden sm:flex items-center gap-1 text-xs bg-background border border-border rounded px-1.5 py-0.5 hover:bg-muted"
            >
              <span>⌘</span>K
            </button>
          )}
        </div>

        {/* Results dropdown */}
        {open && results.length > 0 && (
          <div className="absolute top-full mt-1 left-0 w-80 bg-card border border-border rounded-lg shadow-lg z-50 overflow-hidden">
            {results.map((r) => (
              <button
                key={`${r.type}-${r.id}`}
                onMouseDown={() => handleSelect(r)}
                className="w-full flex items-center gap-3 px-3 py-2 hover:bg-accent text-left transition-colors"
              >
                <span className="text-base flex-shrink-0">{TYPE_ICONS[r.type]}</span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium truncate">{r.label}</p>
                  {r.sub && <p className="text-xs text-muted-foreground truncate">{r.sub}</p>}
                </div>
                <span className="text-xs text-muted-foreground capitalize flex-shrink-0">{r.type}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="flex items-center gap-3">
        {/* Plan badge */}
        <span className="hidden md:inline-flex items-center px-2 py-0.5 text-xs font-medium bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200 rounded-full">
          {plan || "free"}
        </span>

        {/* Tenant ID */}
        <span className="hidden lg:inline text-xs text-muted-foreground font-mono truncate max-w-32">
          {tenantId}
        </span>

        {/* Live pending-approvals counter */}
        <PendingApprovalsBadge />

        {/* Theme toggle */}
        <button
          onClick={toggleTheme}
          className="p-1.5 rounded-md hover:bg-accent transition-colors"
          aria-label={theme === "light" ? "Switch to dark mode" : "Switch to light mode"}
        >
          {theme === "light" ? (
            <Moon className="h-4 w-4" aria-hidden="true" />
          ) : (
            <Sun className="h-4 w-4" aria-hidden="true" />
          )}
        </button>

        {/* Logout */}
        <button
          onClick={handleLogout}
          className="p-1.5 rounded-md hover:bg-accent transition-colors text-muted-foreground"
          aria-label="Sign out"
        >
          <LogOut className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
    </header>
  );
}
