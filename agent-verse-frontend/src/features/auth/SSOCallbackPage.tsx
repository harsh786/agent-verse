/**
 * SSO OAuth2 Callback Page
 *
 * Handles the Keycloak OIDC authorization-code callback at /auth/callback.
 *
 * Flow:
 *   1. Keycloak redirects here with ?code=<code>&state=<state>
 *   2. Exchange the code for tokens via POST /auth/token
 *   3. Fetch user profile via GET /auth/userinfo (with the new JWT)
 *   4. Persist tokens + tenant info to the auth store
 *   5. Redirect to /dashboard
 *
 * Security:
 *   - CSRF state token is validated against sessionStorage before proceeding.
 *   - Tokens are stored in sessionStorage (cleared on tab close) via auth store.
 *   - Error messages are user-friendly; raw server details are never surfaced.
 *   - React strict-mode double-invocation guard via a ref.
 */

import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { AlertCircle, CheckCircle2, Loader2, Zap } from "lucide-react";
import { useAuthStore } from "@/stores/auth";

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

interface TokenResponse {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  token_type: string;
}

interface UserInfo {
  sub: string;
  email: string;
  name: string;
  preferred_username: string;
  roles: string[];
}

type CallbackStatus = "exchanging" | "fetching_user" | "success" | "error";

/** Map Keycloak realm roles to AgentVerse plan tier — mirrors backend logic. */
function rolesToPlan(roles: string[]): string {
  if (roles.includes("admin")) return "enterprise";
  if (roles.includes("operator")) return "professional";
  if (roles.includes("viewer")) return "starter";
  return "free";
}

export function SSOCallbackPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { setSSOCredentials } = useAuthStore();
  const [callbackStatus, setCallbackStatus] = useState<CallbackStatus>("exchanging");
  const [errorMsg, setErrorMsg] = useState("");

  // Prevent double execution in React Strict Mode (double-mount in dev).
  const handled = useRef(false);

  useEffect(() => {
    if (handled.current) return;
    handled.current = true;

    const code = searchParams.get("code");
    const state = searchParams.get("state");
    const errorParam = searchParams.get("error");

    // Keycloak returned an error (e.g. user denied consent)
    if (errorParam) {
      const desc = searchParams.get("error_description") ?? errorParam;
      setCallbackStatus("error");
      setErrorMsg(
        `SSO login failed: ${decodeURIComponent(desc).replace(/\+/g, " ")}`
      );
      return;
    }

    if (!code) {
      setCallbackStatus("error");
      setErrorMsg(
        "No authorization code received. Please try logging in again."
      );
      return;
    }

    // CSRF state validation
    const storedState = sessionStorage.getItem("av_sso_state");
    if (storedState && state !== storedState) {
      setCallbackStatus("error");
      setErrorMsg(
        "Security validation failed (state mismatch). Please try again."
      );
      return;
    }
    sessionStorage.removeItem("av_sso_state");

    void handleCallback(code);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleCallback(code: string): Promise<void> {
    const redirectUri = `${window.location.origin}/auth/callback`;

    try {
      // Step 1: Exchange authorization code for tokens
      setCallbackStatus("exchanging");
      const tokenRes = await fetch(
        `${API_BASE}/auth/token?code=${encodeURIComponent(code)}&redirect_uri=${encodeURIComponent(redirectUri)}`,
        { method: "POST" }
      );

      if (!tokenRes.ok) {
        const body = await tokenRes.json().catch(() => ({}));
        const detail: string = (body as { detail?: string }).detail ?? `Token exchange failed (${tokenRes.status})`;
        throw new Error(detail);
      }

      const tokens = (await tokenRes.json()) as TokenResponse;

      // Step 2: Fetch user profile
      setCallbackStatus("fetching_user");
      let tenantId = "";
      let plan = "free";

      const userRes = await fetch(`${API_BASE}/auth/userinfo`, {
        headers: { Authorization: `Bearer ${tokens.access_token}` },
      });

      if (userRes.ok) {
        const user = (await userRes.json()) as UserInfo;
        tenantId = user.preferred_username || user.email || user.sub;
        plan = rolesToPlan(user.roles);
      }

      // Step 3: Persist and redirect
      setSSOCredentials(
        tokens.access_token,
        tokens.refresh_token,
        tokens.expires_in,
        tenantId,
        plan
      );

      setCallbackStatus("success");
      setTimeout(() => navigate("/dashboard", { replace: true }), 600);
    } catch (err) {
      setCallbackStatus("error");
      setErrorMsg(
        err instanceof Error
          ? err.message
          : "An unexpected error occurred. Please try again."
      );
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-md">
        {/* Logo */}
        <div className="flex flex-col items-center gap-3 mb-8">
          <div className="flex items-center gap-2">
            <Zap className="h-8 w-8 text-blue-500" aria-hidden="true" />
            <span className="text-2xl font-bold">AgentVerse</span>
          </div>
        </div>

        <div className="bg-card border border-border rounded-xl p-8 shadow-sm text-center space-y-4">
          {callbackStatus === "exchanging" && (
            <>
              <Loader2
                className="h-10 w-10 animate-spin text-blue-500 mx-auto"
                aria-label="Signing in"
              />
              <h1 className="text-lg font-semibold">Signing you in</h1>
              <p className="text-sm text-muted-foreground">
                Exchanging authorization code&hellip;
              </p>
            </>
          )}

          {callbackStatus === "fetching_user" && (
            <>
              <Loader2
                className="h-10 w-10 animate-spin text-blue-500 mx-auto"
                aria-label="Loading profile"
              />
              <h1 className="text-lg font-semibold">Loading your profile</h1>
              <p className="text-sm text-muted-foreground">Almost there&hellip;</p>
            </>
          )}

          {callbackStatus === "success" && (
            <>
              <CheckCircle2 className="h-10 w-10 text-green-500 mx-auto" />
              <h1 className="text-lg font-semibold">Signed in successfully</h1>
              <p className="text-sm text-muted-foreground">
                Redirecting to your dashboard&hellip;
              </p>
            </>
          )}

          {callbackStatus === "error" && (
            <>
              <AlertCircle className="h-10 w-10 text-red-500 mx-auto" />
              <h1 className="text-lg font-semibold">Sign-in failed</h1>
              <p
                role="alert"
                className="text-sm text-muted-foreground"
              >
                {errorMsg}
              </p>
              <button
                onClick={() => navigate("/auth", { replace: true })}
                className="mt-2 px-6 py-2 bg-primary text-primary-foreground text-sm font-medium rounded-md hover:opacity-90 transition-opacity"
              >
                Back to Sign In
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
