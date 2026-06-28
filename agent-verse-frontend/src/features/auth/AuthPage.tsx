import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Zap } from "lucide-react";
import { useAuthStore } from "@/stores/auth";

const API_BASE = (import.meta as any).env?.VITE_API_URL ?? 'http://localhost:8000';
const REDIRECT_URI = typeof window !== "undefined"
  ? `${window.location.origin}/auth/callback`
  : "http://localhost:5173/auth/callback";

interface TenantProfile {
  tenant_id: string;
  plan: string;
}

interface SSOConfig {
  sso_enabled: boolean;
  authorization_endpoint: string | null;
}

async function validateCredentials(apiKey: string): Promise<TenantProfile | null> {
  const res = await fetch(`${API_BASE}/tenants/me`, {
    headers: { 'X-API-Key': apiKey },
  });
  if (!res.ok) return null;
  return res.json();
}

async function fetchSSOConfig(): Promise<SSOConfig | null> {
  try {
    const res = await fetch(`${API_BASE}/auth/config`);
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export function AuthPage() {
  const [apiKey, setApiKey] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [ssoEnabled, setSsoEnabled] = useState(false);
  const { setCredentials } = useAuthStore();
  const navigate = useNavigate();

  // Probe the backend to find out if Keycloak SSO is configured
  useEffect(() => {
    void fetchSSOConfig().then((cfg) => {
      if (cfg?.sso_enabled) setSsoEnabled(true);
    });
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmedApiKey = apiKey.trim();
    const trimmedTenantId = tenantId.trim();
    if (!trimmedApiKey || !trimmedTenantId) {
      setError("API key and tenant ID are required.");
      return;
    }

    setError("");
    setIsSubmitting(true);
    try {
      const tenant = await validateCredentials(trimmedApiKey);
      if (!tenant || tenant.tenant_id !== trimmedTenantId) {
        setError("Invalid tenant ID or API key.");
        return;
      }
      setCredentials(trimmedApiKey, tenant.tenant_id, tenant.plan);
      navigate("/dashboard");
    } catch {
      setError("Unable to reach the backend. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  }

  function handleSSOLogin() {
    // Generate CSRF state token and persist so the callback page can validate it
    const state = crypto.randomUUID();
    sessionStorage.setItem("av_sso_state", state);
    // The backend /auth/login endpoint redirects to Keycloak with all required params
    window.location.href = `${API_BASE}/auth/login?redirect_uri=${encodeURIComponent(REDIRECT_URI)}&state=${encodeURIComponent(state)}`;
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

          {/* SSO button — only shown when the backend reports Keycloak is configured */}
          {ssoEnabled && (
            <>
              <button
                type="button"
                onClick={handleSSOLogin}
                className="w-full py-2 px-4 bg-card border border-input text-sm font-medium rounded-md hover:bg-muted/50 transition-colors flex items-center justify-center gap-2"
                aria-label="Sign in with Keycloak SSO"
              >
                <svg
                  viewBox="0 0 24 24"
                  className="h-4 w-4 text-orange-500"
                  fill="currentColor"
                  aria-hidden="true"
                >
                  <path d="M20.003 4.002H4C1.79 4.002 0 5.792 0 8.002v8c0 2.21 1.79 4 4 4h16.003c2.21 0 3.997-1.79 3.997-4v-8c0-2.21-1.787-3.999-3.997-3.999zM12 16.5c-2.485 0-4.5-2.015-4.5-4.5S9.515 7.5 12 7.5s4.5 2.015 4.5 4.5-2.015 4.5-4.5 4.5z" />
                </svg>
                Sign in with Keycloak SSO
              </button>

              <div className="relative my-5">
                <div className="absolute inset-0 flex items-center">
                  <div className="w-full border-t border-border" />
                </div>
                <div className="relative flex justify-center">
                  <span className="bg-card px-3 text-xs text-muted-foreground">
                    or sign in with API key
                  </span>
                </div>
              </div>
            </>
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
              disabled={isSubmitting}
              className="w-full py-2 px-4 bg-primary text-primary-foreground text-sm font-medium rounded-md hover:opacity-90 transition-opacity"
            >
              {isSubmitting ? 'Signing in…' : 'Sign in'}
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
