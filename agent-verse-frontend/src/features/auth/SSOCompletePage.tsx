/**
 * SSO login completion (SAML / Google) at /auth/sso/complete.
 *
 * The backend's SSO callback (SAML ACS, Google callback) verifies the identity,
 * records a session and redirects here with a one-time `?code=` (60 s). This
 * page exchanges it via POST /auth/session/exchange for the session token —
 * the token itself never travels in a URL — and stores it as the Bearer
 * credential.
 */

import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { AlertCircle, Loader2 } from "lucide-react";
import { useAuthStore } from "@/stores/auth";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

interface SessionResponse {
  access_token: string;
  expires_in: number;
  tenant_id: string;
  plan?: string;
}

export function SSOCompletePage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { setSSOCredentials } = useAuthStore();
  const [error, setError] = useState("");
  const handled = useRef(false);

  useEffect(() => {
    if (handled.current) return;
    handled.current = true;
    const code = searchParams.get("code");
    if (!code) {
      setError("No login code received. Please sign in again.");
      return;
    }
    void (async () => {
      try {
        const res = await fetch(`${API_BASE}/auth/session/exchange`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code }),
        });
        if (!res.ok) {
          setError(
            res.status === 401
              ? "This sign-in link has expired or was already used. Please sign in again."
              : "Sign-in could not be completed. Please try again."
          );
          return;
        }
        const data = (await res.json()) as SessionResponse;
        // Session tokens have no refresh token: the user signs in again at expiry.
        setSSOCredentials(data.access_token, "", data.expires_in, data.tenant_id, data.plan ?? "free");
        navigate("/dashboard", { replace: true });
      } catch {
        setError("Unable to reach the backend. Please try again.");
      }
    })();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-md bg-card border border-border rounded-xl p-8 shadow-sm text-center space-y-4">
        {error ? (
          <>
            <AlertCircle className="h-10 w-10 text-red-500 mx-auto" />
            <h1 className="text-lg font-semibold">Sign-in failed</h1>
            <p role="alert" className="text-sm text-muted-foreground">
              {error}
            </p>
            <button
              onClick={() => navigate("/auth", { replace: true })}
              className="mt-2 px-6 py-2 bg-[#00D4FF] text-primary-foreground text-sm font-medium rounded-md hover:opacity-90"
            >
              Back to Sign In
            </button>
          </>
        ) : (
          <>
            <Loader2 className="h-10 w-10 animate-spin text-[#00D4FF] mx-auto" aria-label="Signing in" />
            <h1 className="text-lg font-semibold">Signing you in</h1>
          </>
        )}
      </div>
    </div>
  );
}
