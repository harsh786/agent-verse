import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Zap } from "lucide-react";
import { useAuthStore } from "@/stores/auth";

export function AuthPage() {
  const [apiKey, setApiKey] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [error, setError] = useState("");
  const { login } = useAuthStore();
  const navigate = useNavigate();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!apiKey.trim() || !tenantId.trim()) {
      setError("API key and tenant ID are required.");
      return;
    }
    login({ apiKey: apiKey.trim(), tenantId: tenantId.trim() });
    navigate("/dashboard");
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-md">
        <div className="flex flex-col items-center gap-3 mb-8">
          <div className="flex items-center gap-2">
            <Zap className="h-8 w-8 text-blue-500" aria-hidden="true" />
            <span className="text-2xl font-bold">AgentVerse</span>
          </div>
          <p className="text-muted-foreground text-sm text-center">
            The operating system for autonomous AI agents
          </p>
        </div>

        <div className="bg-card border border-border rounded-xl p-8 shadow-sm">
          <h1 className="text-xl font-semibold mb-6">Sign in to your tenant</h1>

          {error && (
            <div role="alert" className="mb-4 px-3 py-2 text-sm text-red-700 bg-red-50 dark:bg-red-900/20 dark:text-red-400 rounded-md">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label htmlFor="tenantId" className="block text-sm font-medium mb-1.5">
                Tenant ID
              </label>
              <input
                id="tenantId"
                type="text"
                value={tenantId}
                onChange={(e) => setTenantId(e.target.value)}
                placeholder="my-org"
                className="w-full px-3 py-2 text-sm border border-input rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary"
                autoComplete="username"
                required
              />
            </div>

            <div>
              <label htmlFor="apiKey" className="block text-sm font-medium mb-1.5">
                API Key
              </label>
              <input
                id="apiKey"
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="av_key_..."
                className="w-full px-3 py-2 text-sm border border-input rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary"
                autoComplete="current-password"
                required
              />
            </div>

            <button
              type="submit"
              className="w-full py-2 px-4 bg-primary text-primary-foreground text-sm font-medium rounded-md hover:opacity-90 transition-opacity"
            >
              Sign in
            </button>
          </form>

          <p className="mt-4 text-xs text-muted-foreground text-center">
            Don&apos;t have a tenant?{" "}
            <a href="#" className="text-primary underline-offset-2 hover:underline">
              Request access
            </a>
          </p>
        </div>
      </div>
    </div>
  );
}
