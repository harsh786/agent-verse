import { Search, Moon, Sun, LogOut } from "lucide-react";
import { useUiStore } from "@/stores/ui";
import { useAuthStore } from "@/stores/auth";
import { useNavigate } from "react-router-dom";

export function TopBar() {
  const { theme, toggleTheme, openCommandPalette } = useUiStore();
  const { logout, plan, tenantId } = useAuthStore();
  const navigate = useNavigate();

  function handleLogout() {
    logout();
    navigate("/auth");
  }

  return (
    <header className="flex items-center justify-between px-6 py-3 border-b border-border bg-card">
      {/* Command palette trigger */}
      <button
        onClick={openCommandPalette}
        className="flex items-center gap-2 px-3 py-1.5 text-sm text-muted-foreground bg-muted rounded-md hover:bg-accent transition-colors"
        aria-label="Open command palette"
      >
        <Search className="h-4 w-4" aria-hidden="true" />
        <span className="hidden sm:inline">Search or run command...</span>
        <kbd className="hidden sm:inline-flex items-center gap-1 text-xs bg-background border border-border rounded px-1.5 py-0.5">
          <span>⌘</span>K
        </kbd>
      </button>

      <div className="flex items-center gap-3">
        {/* Plan badge */}
        <span className="hidden md:inline-flex items-center px-2 py-0.5 text-xs font-medium bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200 rounded-full">
          {plan || "free"}
        </span>

        {/* Tenant ID */}
        <span className="hidden lg:inline text-xs text-muted-foreground font-mono truncate max-w-32">
          {tenantId}
        </span>

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
