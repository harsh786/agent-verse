/**
 * MFASettings — embedded in Settings > Security tab.
 * Handles: status display, enrollment wizard, code regeneration, disable.
 */
import { useState, useRef, useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Shield, ShieldCheck, ShieldOff, Copy, Eye, EyeOff,
  Loader2, RefreshCw, CheckCircle,
} from 'lucide-react';
import { mfaApi } from '@/lib/api/client';
import { toast } from '@/stores/toast';

type Step = 'idle' | 'scan' | 'verify' | 'recovery-codes' | 'disable' | 'regen';

export function MFASettings() {
  const qc = useQueryClient();
  const [step, setStep] = useState<Step>('idle');
  const [totp, setTotp] = useState('');
  const [disableCode, setDisableCode] = useState('');
  const [regenCode, setRegenCode] = useState('');
  const [enrollData, setEnrollData] = useState<{
    secret: string;
    qr_code: string | null;
    provisioning_uri: string;
  } | null>(null);
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [showSecret, setShowSecret] = useState(false);
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);
  const codeInputRef = useRef<HTMLInputElement>(null);

  const { data: status, isLoading: statusLoading } = useQuery({
    queryKey: ['mfa-status'],
    queryFn: mfaApi.status,
    staleTime: 60_000,
  });

  const enrollMutation = useMutation({
    mutationFn: mfaApi.enroll,
    onSuccess: (data) => {
      setEnrollData({
        secret: data.secret,
        qr_code: data.qr_code,
        provisioning_uri: data.provisioning_uri,
      });
      setStep('scan');
    },
    onError: (e) => toast({ kind: 'error', message: String(e) }),
  });

  const verifyEnrollMutation = useMutation({
    mutationFn: () => mfaApi.verifyEnrollment(totp),
    onSuccess: (data) => {
      setRecoveryCodes(data.recovery_codes);
      setStep('recovery-codes');
      void qc.invalidateQueries({ queryKey: ['mfa-status'] });
      toast({ kind: 'success', message: 'MFA enabled successfully!' });
    },
    onError: () => {
      toast({ kind: 'error', message: 'Invalid code — check your authenticator app' });
      setTotp('');
      codeInputRef.current?.focus();
    },
  });

  const disableMutation = useMutation({
    mutationFn: () => mfaApi.disable(disableCode),
    onSuccess: () => {
      setStep('idle');
      setDisableCode('');
      void qc.invalidateQueries({ queryKey: ['mfa-status'] });
      toast({ kind: 'success', message: 'MFA disabled.' });
    },
    onError: () => toast({ kind: 'error', message: 'Invalid code' }),
  });

  const regenMutation = useMutation({
    mutationFn: () => mfaApi.regenerateCodes(regenCode),
    onSuccess: (data) => {
      setRecoveryCodes(data.recovery_codes);
      setStep('recovery-codes');
      setRegenCode('');
      void qc.invalidateQueries({ queryKey: ['mfa-status'] });
      toast({ kind: 'success', message: 'Recovery codes regenerated.' });
    },
    onError: () => toast({ kind: 'error', message: 'Invalid code' }),
  });

  // Auto-submit TOTP when 6 digits entered
  useEffect(() => {
    if (totp.length === 6 && step === 'verify') {
      verifyEnrollMutation.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [totp]);

  const copyCode = async (code: string, i: number) => {
    await navigator.clipboard.writeText(code).catch(() => {});
    setCopiedIndex(i);
    setTimeout(() => setCopiedIndex(null), 2000);
  };

  const copyAllCodes = async () => {
    await navigator.clipboard.writeText(recoveryCodes.join('\n')).catch(() => {});
    toast({ kind: 'success', message: 'All recovery codes copied!' });
  };

  if (statusLoading) {
    return (
      <div className="h-32 flex items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* MFA Status Card */}
      <div
        className={`p-5 rounded-xl border-2 ${
          status?.enabled
            ? 'border-green-200 bg-green-50/50 dark:border-green-800 dark:bg-green-900/10'
            : 'border-border bg-muted/30'
        }`}
      >
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            {status?.enabled ? (
              <ShieldCheck className="h-8 w-8 text-green-600 dark:text-green-400" />
            ) : (
              <ShieldOff className="h-8 w-8 text-muted-foreground" />
            )}
            <div>
              <h3 className="text-base font-semibold">
                {status?.enabled ? 'MFA Enabled' : 'MFA Disabled'}
              </h3>
              <p className="text-sm text-muted-foreground mt-0.5">
                {status?.enabled
                  ? `${status.recovery_codes_count} recovery code${status.recovery_codes_count !== 1 ? 's' : ''} remaining`
                  : 'Add an extra layer of security to your account'}
              </p>
            </div>
          </div>

          {status?.enabled ? (
            <div className="flex gap-2">
              <button
                onClick={() => setStep('regen')}
                className="text-xs px-3 py-1.5 border border-input rounded-lg hover:bg-muted/50 transition-colors"
              >
                Regenerate codes
              </button>
              <button
                onClick={() => setStep('disable')}
                className="text-xs px-3 py-1.5 border border-red-300 text-red-600 dark:text-red-400 rounded-lg hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
              >
                Disable MFA
              </button>
            </div>
          ) : (
            <button
              onClick={() => enrollMutation.mutate()}
              disabled={enrollMutation.isPending}
              className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground text-sm font-medium rounded-lg hover:opacity-90 disabled:opacity-50"
            >
              {enrollMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Shield className="h-4 w-4" />
              )}
              Enable MFA
            </button>
          )}
        </div>

        {status?.enabled && status.recovery_codes_count <= 3 && (
          <div className="mt-4 p-3 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg">
            <p className="text-xs text-amber-700 dark:text-amber-400">
              &#9888;&#65039; Only {status.recovery_codes_count} recovery code
              {status.recovery_codes_count !== 1 ? 's' : ''} remaining. Regenerate them before
              you run out.
            </p>
          </div>
        )}
      </div>

      {/* STEP: Scan QR Code */}
      {step === 'scan' && enrollData && (
        <div className="p-5 border border-border rounded-xl space-y-4">
          <h3 className="font-semibold">Step 1: Scan QR Code</h3>
          <p className="text-sm text-muted-foreground">
            Open your authenticator app (Google Authenticator, Authy, 1Password, etc.) and scan
            this QR code.
          </p>

          <div className="flex justify-center">
            {enrollData.qr_code ? (
              <img
                src={enrollData.qr_code}
                alt="MFA QR Code"
                className="w-48 h-48 border border-border rounded-lg bg-[#0F1826] p-2"
              />
            ) : (
              <div className="w-48 h-48 border border-border rounded-lg bg-muted flex items-center justify-center">
                <p className="text-xs text-center text-muted-foreground px-4">
                  QR code unavailable. Use the manual key below.
                </p>
              </div>
            )}
          </div>

          {/* Manual entry key */}
          <div className="bg-muted/50 rounded-lg p-3">
            <p className="text-xs font-medium mb-2">Manual entry key</p>
            <div className="flex items-center gap-2">
              <code
                className={`text-xs font-mono flex-1 ${showSecret ? '' : 'blur-sm select-none'}`}
              >
                {enrollData.secret.match(/.{1,4}/g)?.join(' ')}
              </code>
              <button
                onClick={() => setShowSecret((v) => !v)}
                className="text-muted-foreground hover:text-foreground"
                aria-label={showSecret ? 'Hide secret' : 'Show secret'}
              >
                {showSecret ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
              {showSecret && (
                <button
                  onClick={() => {
                    void navigator.clipboard?.writeText(enrollData.secret);
                    toast({ kind: 'success', message: 'Secret copied' });
                  }}
                  className="text-muted-foreground hover:text-foreground"
                  aria-label="Copy secret"
                >
                  <Copy className="h-4 w-4" />
                </button>
              )}
            </div>
          </div>

          <button
            onClick={() => setStep('verify')}
            className="w-full py-2.5 bg-primary text-primary-foreground font-medium rounded-lg hover:opacity-90"
          >
            I&apos;ve scanned the QR code &rarr;
          </button>
        </div>
      )}

      {/* STEP: Verify first code */}
      {step === 'verify' && (
        <div className="p-5 border border-border rounded-xl space-y-4">
          <h3 className="font-semibold">Step 2: Verify your code</h3>
          <p className="text-sm text-muted-foreground">
            Enter the 6-digit code shown in your authenticator app.
          </p>
          <input
            ref={codeInputRef}
            autoFocus
            value={totp}
            onChange={(e) => setTotp(e.target.value.replace(/\D/g, '').slice(0, 6))}
            maxLength={6}
            inputMode="numeric"
            placeholder="000000"
            className="w-full text-center text-3xl tracking-[0.5em] font-mono py-4 border-2 border-input rounded-xl bg-background focus:outline-none focus:border-primary"
            aria-label="TOTP code"
          />
          <div className="flex gap-3">
            <button
              onClick={() => setStep('scan')}
              className="flex-1 py-2.5 border border-input rounded-lg hover:bg-muted/50"
            >
              &larr; Back
            </button>
            <button
              onClick={() => verifyEnrollMutation.mutate()}
              disabled={totp.length < 6 || verifyEnrollMutation.isPending}
              className="flex-1 py-2.5 bg-primary text-primary-foreground font-medium rounded-lg hover:opacity-90 disabled:opacity-50 flex items-center justify-center gap-2"
            >
              {verifyEnrollMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : null}
              Verify &amp; Enable
            </button>
          </div>
        </div>
      )}

      {/* STEP: Recovery Codes */}
      {step === 'recovery-codes' && recoveryCodes.length > 0 && (
        <div className="p-5 border-2 border-green-200 dark:border-green-800 rounded-xl space-y-4">
          <div className="flex items-center gap-2">
            <CheckCircle className="h-5 w-5 text-green-600" />
            <h3 className="font-semibold text-green-700 dark:text-green-400">
              MFA Enabled &mdash; Save Your Recovery Codes
            </h3>
          </div>
          <p className="text-sm text-muted-foreground">
            Store these recovery codes in a safe place. Each code can only be used once. Use them
            if you lose access to your authenticator app.
          </p>
          <div className="grid grid-cols-2 gap-2">
            {recoveryCodes.map((code, i) => (
              <button
                key={i}
                onClick={() => void copyCode(code, i)}
                className="flex items-center justify-between p-2.5 bg-muted/50 rounded-lg font-mono text-sm hover:bg-muted transition-colors group"
              >
                <span>{code}</span>
                {copiedIndex === i ? (
                  <CheckCircle className="h-3.5 w-3.5 text-green-600" />
                ) : (
                  <Copy className="h-3.5 w-3.5 text-muted-foreground opacity-0 group-hover:opacity-100" />
                )}
              </button>
            ))}
          </div>
          <button
            onClick={() => void copyAllCodes()}
            className="w-full py-2 border border-input rounded-lg text-sm hover:bg-muted/50 flex items-center justify-center gap-2"
          >
            <Copy className="h-4 w-4" /> Copy all codes
          </button>
          <button
            onClick={() => { setStep('idle'); setRecoveryCodes([]); }}
            className="w-full py-2.5 bg-primary text-primary-foreground rounded-lg font-medium hover:opacity-90"
          >
            Done &mdash; I&apos;ve saved my recovery codes
          </button>
        </div>
      )}

      {/* STEP: Disable MFA */}
      {step === 'disable' && (
        <div className="p-5 border border-red-200 dark:border-red-800 rounded-xl space-y-4">
          <h3 className="font-semibold text-red-700 dark:text-red-400">Disable MFA</h3>
          <p className="text-sm text-muted-foreground">
            Enter your current TOTP code to disable MFA.
          </p>
          <input
            autoFocus
            value={disableCode}
            onChange={(e) => setDisableCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
            maxLength={6}
            inputMode="numeric"
            placeholder="000000"
            className="w-full text-center text-3xl tracking-[0.5em] font-mono py-4 border-2 border-input rounded-xl bg-background focus:outline-none focus:border-primary"
            aria-label="TOTP code to disable MFA"
          />
          <div className="flex gap-3">
            <button
              onClick={() => { setStep('idle'); setDisableCode(''); }}
              className="flex-1 py-2.5 border border-input rounded-lg hover:bg-muted/50"
            >
              Cancel
            </button>
            <button
              onClick={() => disableMutation.mutate()}
              disabled={disableCode.length < 6 || disableMutation.isPending}
              className="flex-1 py-2.5 bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50 flex items-center justify-center gap-2"
            >
              {disableMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : null}
              Disable MFA
            </button>
          </div>
        </div>
      )}

      {/* STEP: Regenerate codes */}
      {step === 'regen' && (
        <div className="p-5 border border-border rounded-xl space-y-4">
          <h3 className="font-semibold">Regenerate Recovery Codes</h3>
          <p className="text-sm text-muted-foreground">
            Enter your TOTP code to generate new recovery codes. Your old codes will be
            invalidated.
          </p>
          <input
            autoFocus
            value={regenCode}
            onChange={(e) => setRegenCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
            maxLength={6}
            inputMode="numeric"
            placeholder="000000"
            className="w-full text-center text-3xl tracking-[0.5em] font-mono py-4 border-2 border-input rounded-xl bg-background focus:outline-none focus:border-primary"
            aria-label="TOTP code to regenerate recovery codes"
          />
          <div className="flex gap-3">
            <button
              onClick={() => { setStep('idle'); setRegenCode(''); }}
              className="flex-1 py-2.5 border border-input rounded-lg hover:bg-muted/50"
            >
              Cancel
            </button>
            <button
              onClick={() => regenMutation.mutate()}
              disabled={regenCode.length < 6 || regenMutation.isPending}
              className="flex-1 py-2.5 bg-primary text-primary-foreground rounded-lg hover:opacity-90 disabled:opacity-50 flex items-center justify-center gap-2"
            >
              {regenMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
              Regenerate
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
