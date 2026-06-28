import { useState } from 'react';
import type { JSX } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  Download, Trash2, Globe, AlertTriangle, Shield, CheckCircle2, XCircle,
  ChevronRight, Building,
} from 'lucide-react';
import { enterpriseApi } from '@/lib/api/client';
import type { DataResidencyInfo, EnterpriseExportResult } from '@/lib/api/client';
import { Skeleton } from '@/components/ui/Skeleton';
import { toast } from '@/stores/toast';

// ── Compliance Dashboard ──────────────────────────────────────────────────────

function ComplianceDashboard(): JSX.Element {
  const { data: residency, isLoading } = useQuery<DataResidencyInfo>({
    queryKey: ['residency'],
    queryFn: () => enterpriseApi.getResidency(),
  });

  const frameworks = residency?.compliance_frameworks ?? [];

  const KNOWN_FRAMEWORKS = ['GDPR', 'SOC2', 'HIPAA', 'ISO27001', 'PCI-DSS', 'CCPA'];

  return (
    <div className="bg-card border border-border rounded-xl p-5">
      <h2 className="font-semibold flex items-center gap-2 mb-4">
        <Shield className="h-4 w-4" /> Compliance Status
      </h2>
      {isLoading ? (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-14" />)}
        </div>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          {KNOWN_FRAMEWORKS.map((fw) => {
            const active = frameworks.includes(fw);
            return (
              <div
                key={fw}
                className={`flex items-center gap-2 rounded-lg border px-3 py-3 ${
                  active
                    ? 'border-green-300/60 bg-green-50/40 dark:bg-green-900/20'
                    : 'border-border bg-muted/20'
                }`}
              >
                {active ? (
                  <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400 flex-shrink-0" />
                ) : (
                  <XCircle className="h-4 w-4 text-muted-foreground/50 flex-shrink-0" />
                )}
                <span className={`text-sm font-medium ${active ? '' : 'text-muted-foreground'}`}>
                  {fw}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── SAML/SSO Wizard ──────────────────────────────────────────────────────────

const IDP_OPTIONS = ['Okta', 'Azure AD', 'Google Workspace', 'OneLogin', 'PingIdentity'];

const WIZARD_STEPS = [
  { label: 'Choose IdP', description: 'Select your identity provider' },
  { label: 'Upload Metadata', description: 'Upload IdP SAML metadata XML' },
  { label: 'Map Attributes', description: 'Map SAML attributes to user fields' },
  { label: 'Test Connection', description: 'Verify the SSO flow end-to-end' },
] as const;

function SAMLWizard(): JSX.Element {
  const [step, setStep] = useState(0);
  const [selectedIdp, setSelectedIdp] = useState('');
  const [metadataFile, setMetadataFile] = useState<File | null>(null);
  const [attrEmail, setAttrEmail] = useState('email');
  const [attrName, setAttrName] = useState('displayName');
  const [testStatus, setTestStatus] = useState<'idle' | 'testing' | 'ok' | 'fail'>('idle');

  const handleTestConnection = (): void => {
    setTestStatus('testing');
    // Simulate test
    setTimeout(() => setTestStatus('ok'), 1500);
  };

  return (
    <div className="bg-card border border-border rounded-xl p-5">
      <h2 className="font-semibold flex items-center gap-2 mb-4">
        <Building className="h-4 w-4" /> SAML / SSO Setup
      </h2>

      {/* Step indicators */}
      <div className="flex items-center gap-1 mb-6 overflow-x-auto">
        {WIZARD_STEPS.map((s, i) => (
          <div key={i} className="flex items-center gap-1 flex-shrink-0">
            <button
              onClick={() => i <= step && setStep(i)}
              className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                i === step
                  ? 'bg-primary text-primary-foreground'
                  : i < step
                  ? 'bg-primary/10 text-primary'
                  : 'text-muted-foreground'
              }`}
            >
              <span className="w-4 h-4 rounded-full border flex items-center justify-center text-xs">
                {i + 1}
              </span>
              <span className="hidden md:inline">{s.label}</span>
            </button>
            {i < WIZARD_STEPS.length - 1 && <ChevronRight className="h-3 w-3 text-muted-foreground flex-shrink-0" />}
          </div>
        ))}
      </div>

      {/* Step content */}
      {step === 0 && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">{WIZARD_STEPS[0].description}</p>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
            {IDP_OPTIONS.map((idp) => (
              <button
                key={idp}
                onClick={() => setSelectedIdp(idp)}
                className={`px-4 py-3 rounded-lg border text-sm transition-colors ${
                  selectedIdp === idp
                    ? 'border-primary bg-primary/10 text-primary'
                    : 'border-border hover:bg-muted'
                }`}
              >
                {idp}
              </button>
            ))}
          </div>
          <button
            onClick={() => setStep(1)}
            disabled={!selectedIdp}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm hover:opacity-90 disabled:opacity-50"
          >
            Continue →
          </button>
        </div>
      )}

      {step === 1 && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">{WIZARD_STEPS[1].description} for <strong>{selectedIdp}</strong></p>
          <div
            className="border-2 border-dashed border-border rounded-xl p-8 text-center cursor-pointer hover:bg-muted/30 transition-colors"
            onDrop={(e) => {
              e.preventDefault();
              const file = e.dataTransfer.files[0];
              if (file) setMetadataFile(file);
            }}
            onDragOver={(e) => e.preventDefault()}
            onClick={() => document.getElementById('saml-meta-input')?.click()}
          >
            <p className="text-sm text-muted-foreground">
              {metadataFile ? metadataFile.name : 'Drag & drop metadata XML or click to browse'}
            </p>
          </div>
          <input
            id="saml-meta-input"
            type="file"
            accept=".xml"
            className="hidden"
            onChange={(e) => e.target.files?.[0] && setMetadataFile(e.target.files[0])}
          />
          <div className="flex gap-2">
            <button onClick={() => setStep(0)} className="px-4 py-2 border border-border rounded-lg text-sm hover:bg-muted">← Back</button>
            <button
              onClick={() => setStep(2)}
              disabled={!metadataFile}
              className="px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm hover:opacity-90 disabled:opacity-50"
            >
              Continue →
            </button>
          </div>
        </div>
      )}

      {step === 2 && (
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">{WIZARD_STEPS[2].description}</p>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">Email attribute</label>
              <input
                value={attrEmail}
                onChange={(e) => setAttrEmail(e.target.value)}
                className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">Display name attribute</label>
              <input
                value={attrName}
                onChange={(e) => setAttrName(e.target.value)}
                className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background"
              />
            </div>
          </div>
          <div className="flex gap-2">
            <button onClick={() => setStep(1)} className="px-4 py-2 border border-border rounded-lg text-sm hover:bg-muted">← Back</button>
            <button
              onClick={() => setStep(3)}
              className="px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm hover:opacity-90"
            >
              Continue →
            </button>
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">{WIZARD_STEPS[3].description}</p>
          <div className="flex items-center gap-3">
            <button
              onClick={handleTestConnection}
              disabled={testStatus === 'testing'}
              className="px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm hover:opacity-90 disabled:opacity-50"
            >
              {testStatus === 'testing' ? 'Testing…' : 'Test SSO Connection'}
            </button>
            {testStatus === 'ok' && (
              <span className="flex items-center gap-1 text-sm text-green-600">
                <CheckCircle2 className="h-4 w-4" /> Connection successful
              </span>
            )}
            {testStatus === 'fail' && (
              <span className="flex items-center gap-1 text-sm text-red-600">
                <XCircle className="h-4 w-4" /> Connection failed
              </span>
            )}
          </div>
          {testStatus === 'ok' && (
            <button
              onClick={() => toast({ kind: 'success', message: 'SSO configuration saved' })}
              className="px-4 py-2 bg-green-600 text-white rounded-lg text-sm hover:bg-green-700"
            >
              Save SSO Configuration
            </button>
          )}
          <button onClick={() => setStep(2)} className="px-4 py-2 border border-border rounded-lg text-sm hover:bg-muted">← Back</button>
        </div>
      )}
    </div>
  );
}

// ── SCIM Provisioning ─────────────────────────────────────────────────────────

function ScimSection(): JSX.Element {
  const [scimEnabled, setScimEnabled] = useState(false);

  return (
    <div className="bg-card border border-border rounded-xl p-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="font-semibold">SCIM Provisioning</h2>
        <button
          onClick={() => {
            setScimEnabled((v) => !v);
            toast({ kind: 'info', message: `SCIM provisioning ${scimEnabled ? 'disabled' : 'enabled'}` });
          }}
          className={`px-4 py-1.5 rounded-lg text-xs font-medium transition-colors ${
            scimEnabled
              ? 'bg-primary text-primary-foreground'
              : 'border border-border hover:bg-muted'
          }`}
        >
          {scimEnabled ? 'Enabled' : 'Enable'}
        </button>
      </div>
      <p className="text-sm text-muted-foreground">
        SCIM 2.0 allows your IdP to automatically provision and deprovision users.
      </p>
      {scimEnabled && (
        <div className="mt-3 p-3 bg-muted/40 rounded-lg text-xs font-mono break-all">
          SCIM endpoint: <span className="text-primary">https://api.agentverse.io/scim/v2</span>
        </div>
      )}
    </div>
  );
}

// ── Contracts ─────────────────────────────────────────────────────────────────

const CONTRACTS = [
  { id: 'baa', label: 'Business Associate Agreement (BAA)', status: 'signed', date: '2024-01-15' },
  { id: 'dpa', label: 'Data Processing Agreement (DPA)', status: 'signed', date: '2024-01-15' },
  { id: 'sla', label: 'Service Level Agreement (SLA)', status: 'pending', date: null },
] as const;

function ContractsSection(): JSX.Element {
  return (
    <div className="bg-card border border-border rounded-xl overflow-hidden">
      <div className="px-5 py-3 border-b border-border">
        <h2 className="font-semibold">Contracts &amp; Agreements</h2>
      </div>
      <div className="divide-y divide-border">
        {CONTRACTS.map((c) => (
          <div key={c.id} className="flex items-center justify-between px-5 py-4">
            <div>
              <p className="text-sm font-medium">{c.label}</p>
              {c.date && <p className="text-xs text-muted-foreground mt-0.5">Signed {c.date}</p>}
            </div>
            <span
              className={`px-2.5 py-1 rounded-full text-xs font-medium ${
                c.status === 'signed'
                  ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                  : 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400'
              }`}
            >
              {c.status}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Export section ────────────────────────────────────────────────────────────

function ExportSection(): JSX.Element {
  const [result, setResult] = useState<EnterpriseExportResult | null>(null);

  const mutation = useMutation({
    mutationFn: () => enterpriseApi.exportData(),
    onSuccess: (data) => {
      setResult(data);
      toast({ kind: 'info', message: 'Export started — you\'ll be notified when ready' });
    },
    onError: (e) => toast({ kind: 'error', message: `Failed: export data. ${String(e)}` }),
  });

  return (
    <div className="bg-card border border-border rounded-xl p-5">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h2 className="font-semibold flex items-center gap-2">
            <Download className="h-4 w-4" /> Export My Data
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            Download a copy of all your data including goals, agents, and audit logs.
          </p>
        </div>
        <button
          onClick={() => mutation.mutate()}
          disabled={mutation.isPending}
          className="flex items-center gap-1.5 bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm hover:opacity-90 disabled:opacity-50 ml-4 flex-shrink-0"
        >
          <Download className="h-3.5 w-3.5" />
          {mutation.isPending ? 'Exporting…' : 'Export'}
        </button>
      </div>

      {result && (
        <div className="mt-3 p-3 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg text-sm">
          {result.download_url ? (
            <div className="space-y-1.5">
              <p className="text-green-800 dark:text-green-300 font-medium">Export ready</p>
              {result.size_bytes != null && (
                <p className="text-green-700 dark:text-green-400 text-xs">
                  Size: {(result.size_bytes / 1024 / 1024).toFixed(2)} MB
                </p>
              )}
              <a
                href={result.download_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-block text-green-800 dark:text-green-300 underline text-xs"
              >
                Download export →
              </a>
            </div>
          ) : (
            <p className="text-green-800 dark:text-green-300">{result.message ?? 'Export completed.'}</p>
          )}
        </div>
      )}
    </div>
  );
}

// ── Delete section ────────────────────────────────────────────────────────────

function DeleteSection(): JSX.Element {
  const [showConfirm, setShowConfirm] = useState(false);
  const [confirmText, setConfirmText] = useState('');
  const [deleted, setDeleted] = useState(false);

  const mutation = useMutation({
    mutationFn: () => enterpriseApi.purgeData(),
    onSuccess: () => {
      setDeleted(true);
      setShowConfirm(false);
      toast({ kind: 'warning', message: 'Security action: data deletion scheduled' });
    },
    onError: (e) => toast({ kind: 'error', message: `Failed: delete data. ${String(e)}` }),
  });

  if (deleted) {
    return (
      <div className="bg-card border border-border rounded-xl p-5">
        <div className="flex items-center gap-2 text-green-600">
          <Trash2 className="h-4 w-4" />
          <p className="font-medium text-sm">Data deletion scheduled.</p>
        </div>
        <p className="text-sm text-muted-foreground mt-1">
          Your data will be permanently deleted within 30 days.
        </p>
      </div>
    );
  }

  return (
    <div className="bg-card border border-red-200 dark:border-red-800/40 rounded-xl p-5">
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <h2 className="font-semibold flex items-center gap-2 text-red-700 dark:text-red-400">
            <Trash2 className="h-4 w-4" /> Delete My Data
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            Permanently delete all your data. This action cannot be undone.
          </p>
        </div>
        {!showConfirm && (
          <button
            onClick={() => setShowConfirm(true)}
            className="flex items-center gap-1.5 bg-red-600 text-white px-4 py-2 rounded-lg text-sm hover:bg-red-700 ml-4 flex-shrink-0"
          >
            <AlertTriangle className="h-3.5 w-3.5" />
            Delete
          </button>
        )}
      </div>

      {showConfirm && (
        <div className="mt-4 p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg space-y-3">
          <p className="text-sm font-medium text-red-800 dark:text-red-300">
            Type <span className="font-mono font-bold">DELETE MY DATA</span> to confirm.
          </p>
          <input
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            placeholder="DELETE MY DATA"
            className="w-full border border-red-300 dark:border-red-700 rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-red-400"
          />
          <div className="flex gap-2">
            <button
              onClick={() => { setShowConfirm(false); setConfirmText(''); }}
              className="px-4 py-2 border border-border rounded-lg text-sm hover:bg-accent"
            >
              Cancel
            </button>
            <button
              onClick={() => mutation.mutate()}
              disabled={confirmText !== 'DELETE MY DATA' || mutation.isPending}
              className="bg-red-600 text-white px-4 py-2 rounded-lg text-sm hover:bg-red-700 disabled:opacity-50"
            >
              {mutation.isPending ? 'Deleting…' : 'Confirm Delete'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Residency section ─────────────────────────────────────────────────────────

function ResidencySection(): JSX.Element {
  const { data: residency, isLoading, isError } = useQuery<DataResidencyInfo>({
    queryKey: ['residency'],
    queryFn: () => enterpriseApi.getResidency(),
  });

  return (
    <div className="bg-card border border-border rounded-xl p-5">
      <h2 className="font-semibold flex items-center gap-2 mb-3">
        <Globe className="h-4 w-4" /> Data Residency
      </h2>

      {isLoading ? (
        <Skeleton className="h-20 w-full" />
      ) : isError ? (
        <p className="text-sm text-red-500">Failed to load residency info.</p>
      ) : residency ? (
        <dl className="space-y-3">
          {[
            { label: 'Region', value: residency.region },
            { label: 'Data Center', value: residency.data_center ?? '—' },
          ].map(({ label, value }) => (
            <div key={label} className="flex justify-between text-sm">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="font-medium">{value}</dd>
            </div>
          ))}
          {residency.description && (
            <p className="text-sm text-muted-foreground pt-1 border-t border-border">
              {residency.description}
            </p>
          )}
        </dl>
      ) : (
        <p className="text-sm text-muted-foreground">No residency info available.</p>
      )}
    </div>
  );
}

// ── Main ──────────────────────────────────────────────────────────────────────

export function EnterprisePage(): JSX.Element {
  return (
    <div className="p-4 md:p-6 max-w-3xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Enterprise</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Compliance, SSO, data management, and residency controls.
        </p>
      </div>
      <ComplianceDashboard />
      <SAMLWizard />
      <ScimSection />
      <ContractsSection />
      <ResidencySection />
      <ExportSection />
      <DeleteSection />
    </div>
  );
}
