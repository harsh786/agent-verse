import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Search, X } from "lucide-react";
import { useUiStore } from "@/stores/ui";

const COMMANDS = [
  { label: "Go to Dashboard", action: "/dashboard" },
  { label: "Submit a goal", action: "/goals" },
  { label: "Create agent (meta-agent)", action: "/agents/create" },
  { label: "Browse connector catalog", action: "/connectors/catalog" },
  { label: "Manage schedules", action: "/schedules" },
  { label: "Search knowledge", action: "/knowledge" },
  { label: "Review HITL approvals", action: "/governance" },
  { label: "View observability", action: "/observability" },
  { label: "Open marketplace", action: "/marketplace" },
  { label: "Account settings", action: "/settings" },
];

export function CommandPalette() {
  const { commandPaletteOpen, closeCommandPalette } = useUiStore();
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  // Open with ⌘K
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        if (commandPaletteOpen) closeCommandPalette();
        else useUiStore.getState().openCommandPalette();
      }
      if (e.key === "Escape") closeCommandPalette();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [commandPaletteOpen, closeCommandPalette]);

  useEffect(() => {
    if (commandPaletteOpen) {
      setQuery("");
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [commandPaletteOpen]);

  if (!commandPaletteOpen) return null;

  const filtered = COMMANDS.filter((c) =>
    c.label.toLowerCase().includes(query.toLowerCase())
  );

  function select(path: string) {
    closeCommandPalette();
    navigate(path);
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      className="fixed inset-0 z-50 flex items-start justify-center pt-20 px-4"
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/50"
        onClick={closeCommandPalette}
        aria-hidden="true"
      />

      {/* Panel */}
      <div className="relative w-full max-w-lg bg-card border border-border rounded-xl shadow-2xl overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
          <Search className="h-5 w-5 text-muted-foreground flex-shrink-0" aria-hidden="true" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search commands..."
            className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            aria-label="Command search"
          />
          <button onClick={closeCommandPalette} aria-label="Close command palette">
            <X className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          </button>
        </div>

        <ul role="listbox" className="max-h-72 overflow-y-auto py-2">
          {filtered.length === 0 ? (
            <li className="px-4 py-3 text-sm text-muted-foreground">No results found.</li>
          ) : (
            filtered.map((cmd) => (
              <li key={cmd.action}>
                <button
                  role="option"
                  aria-selected="false"
                  onClick={() => select(cmd.action)}
                  className="w-full text-left px-4 py-2.5 text-sm hover:bg-accent transition-colors"
                >
                  {cmd.label}
                </button>
              </li>
            ))
          )}
        </ul>
      </div>
    </div>
  );
}
