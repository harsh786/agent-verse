/**
 * MFAVerifyPage — shown when a user has authenticated but MFA verification is required.
 *
 * Flow: Login → (MFA required) → this page → verify code → app
 */
import { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { Shield, Loader2, KeyRound, RefreshCw } from 'lucide-react';
import { mfaApi } from '@/lib/api/client';
import { useAuthStore } from '@/stores/auth';
import { toast } from '@/stores/toast';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';

export default function MFAVerifyPage() {
  const navigate = useNavigate();
  const { setMfaRequired, setMfaToken } = useAuthStore();
  const [code, setCode] = useState('');
  const [mode, setMode] = useState<'totp' | 'recovery'>('totp');
  const inputRef = useRef<HTMLInputElement>(null);

  const verifyMutation = useMutation({
    mutationFn: () => mfaApi.verify(code.trim()),
    onSuccess: (data) => {
      setMfaRequired(false);
      setMfaToken(null);
      toast({ kind: 'success', message: 'Verified! Welcome back.' });
      if (data.remaining_recovery_codes !== undefined && data.remaining_recovery_codes <= 3) {
        toast({
          kind: 'warning',
          message: `Only ${data.remaining_recovery_codes} recovery codes remaining. Regenerate soon.`,
        });
      }
      navigate('/goals', { replace: true });
    },
    onError: () => {
      toast({ kind: 'error', message: 'Invalid code. Please try again.' });
      setCode('');
      inputRef.current?.focus();
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!code.trim()) return;
    verifyMutation.mutate();
  };

  // Auto-submit when 6 digits entered (TOTP mode)
  const handleCodeChange = (v: string) => {
    const clean = mode === 'totp' ? v.replace(/\D/g, '').slice(0, 6) : v.slice(0, 11);
    setCode(clean);
    if (mode === 'totp' && clean.length === 6) {
      setTimeout(() => verifyMutation.mutate(), 100);
    }
  };

  return (
    <JARVISPageShell>
    <div className="min-h-screen bg-background flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        {/* Header */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-primary/10 mb-4">
            <Shield className="h-8 w-8 text-primary" />
          </div>
          <h1 className="text-2xl font-bold">Two-Factor Authentication</h1>
          <p className="text-muted-foreground mt-2">
            {mode === 'totp'
              ? 'Enter the 6-digit code from your authenticator app'
              : 'Enter one of your recovery codes'}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            {mode === 'totp' ? (
              <div className="flex gap-2 justify-center">
                {/* Single wide input for TOTP */}
                <input
                  ref={inputRef}
                  value={code}
                  onChange={(e) => handleCodeChange(e.target.value)}
                  maxLength={6}
                  inputMode="numeric"
                  pattern="\d{6}"
                  autoComplete="one-time-code"
                  autoFocus
                  className="w-full text-center text-3xl tracking-[0.5em] font-mono px-4 py-4 border-2 border-input rounded-xl bg-background focus:outline-none focus:border-primary transition-colors"
                  placeholder="000000"
                  aria-label="TOTP verification code"
                />
              </div>
            ) : (
              <input
                ref={inputRef}
                value={code}
                onChange={(e) => setCode(e.target.value.toUpperCase())}
                maxLength={11}
                autoFocus
                className="w-full text-center text-xl tracking-widest font-mono px-4 py-4 border-2 border-input rounded-xl bg-background focus:outline-none focus:border-primary transition-colors"
                placeholder="XXXXX-XXXXX"
                aria-label="Recovery code"
              />
            )}
          </div>

          <button
            type="submit"
            disabled={verifyMutation.isPending || code.length < (mode === 'totp' ? 6 : 11)}
            className="w-full py-3 bg-primary text-primary-foreground font-medium rounded-xl hover:opacity-90 disabled:opacity-50 transition-opacity flex items-center justify-center gap-2"
          >
            {verifyMutation.isPending ? (
              <>
                <Loader2 className="h-5 w-5 animate-spin" /> Verifying&hellip;
              </>
            ) : (
              <>
                <Shield className="h-5 w-5" /> Verify
              </>
            )}
          </button>
        </form>

        {/* Mode toggle */}
        <div className="mt-6 text-center">
          {mode === 'totp' ? (
            <button
              type="button"
              onClick={() => { setMode('recovery'); setCode(''); }}
              className="text-sm text-muted-foreground hover:text-primary transition-colors flex items-center gap-1 mx-auto"
            >
              <KeyRound className="h-4 w-4" />
              Use a recovery code instead
            </button>
          ) : (
            <button
              type="button"
              onClick={() => { setMode('totp'); setCode(''); }}
              className="text-sm text-muted-foreground hover:text-primary transition-colors flex items-center gap-1 mx-auto"
            >
              <RefreshCw className="h-4 w-4" />
              Use authenticator app instead
            </button>
          )}
        </div>

        <p className="text-xs text-center text-muted-foreground mt-8">
          Having trouble?{' '}
          <a href="mailto:support@agentverse.ai" className="text-primary hover:underline">
            Contact support
          </a>
        </p>
      </div>
    </div>
    </JARVISPageShell>
  );
}
