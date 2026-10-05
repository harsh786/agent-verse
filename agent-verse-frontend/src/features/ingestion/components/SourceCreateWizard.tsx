import { useState } from 'react';
import { X, ChevronRight } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { SPRING_PAGE } from '@/components/ui/JARVISPageShell';
import type { SourceConfig, SourceFamily, SourceValidation } from '../types';
import { FAMILY_CONFIG, ALL_FAMILIES } from '../types';
import { useCreateSource, useSourcePreview, useValidateSource } from '../hooks';
import { FieldError } from './families/fields';
import { FamilyFormRouter } from './families/FamilyFormRouter';
import { formShowsFieldErrors } from './families/formSupport';
import { FriendlyErrorMessage } from '@/components/ui/FriendlyErrorMessage';
import { connectionConfigErrors, parseApiFieldErrors, type ApiFieldErrors } from '@/lib/apiFieldErrors';

interface Props { onClose: () => void; onCreated?: () => void; }

type Step = 'family' | 'type' | 'configure' | 'preview';

const SOURCE_TYPES_BY_FAMILY: Record<SourceFamily, string[]> = {
  object_storage:  ['s3', 'gcs', 'azure_blob', 'minio', 'r2', 'delta_lake', 'iceberg'],
  olap_database:   ['snowflake', 'bigquery', 'clickhouse', 'databricks', 'redshift', 'duckdb', 'trino'],
  oltp_database:   ['postgresql', 'mysql', 'mssql', 'oracle', 'mongodb', 'cockroachdb'],
  nosql_database:  ['mongodb', 'redis', 'elasticsearch', 'opensearch', 'dynamodb', 'firestore', 'cosmos_db', 'cassandra'],
  streaming:       ['kafka', 'kinesis', 'pubsub', 'pulsar', 'rabbitmq', 'nats'],
  file_system:     ['local_fs', 'sftp', 'nfs'],
  document_store:  ['gdrive', 'notion', 'confluence', 'sharepoint', 'dropbox', 'box'],
  communication:   ['slack', 'teams', 'discord', 'email_imap', 'gmail', 'zoom'],
  code_repository: ['github', 'gitlab', 'bitbucket', 'jira', 'github_issues'],
  web:             ['web_crawl', 'rss', 'arxiv', 'pubmed', 'twitter', 'reddit'],
  crm_erp:         ['salesforce', 'hubspot', 'sap', 'workday', 'quickbooks', 'asana'],
  support:         ['zendesk', 'intercom', 'servicenow', 'freshdesk', 'helpscout'],
  iot_telemetry:   ['mqtt', 'influxdb', 'opcua', 'modbus'],
  observability:   ['pagerduty', 'sentry', 'grafana', 'prometheus', 'datadog'],
  scientific:      ['arxiv', 'pubmed', 'fhir', 'sec_edgar', 'uspto'],
  graph_database:  ['neo4j', 'neptune', 'tigergraph', 'wikidata', 'arangodb'],
  vector_database: ['pinecone', 'weaviate', 'qdrant', 'chroma', 'milvus'],
  agent_generated: ['agent_generated', 'pdf_file', 'docx_file'],
};

export function SourceCreateWizard({ onClose, onCreated }: Props) {
  const [step, setStep] = useState<Step>('family');
  const [selectedFamily, setSelectedFamily] = useState<SourceFamily | null>(null);
  const [selectedType, setSelectedType] = useState<string | null>(null);
  const [sourceName, setSourceName] = useState('');
  const [connConfig, setConnConfig] = useState<Record<string, unknown>>({});
  const [collectionId, setCollectionId] = useState('');
  const [syncMode, setSyncMode] = useState<'incremental' | 'full' | 'streaming'>('incremental');

  const create = useCreateSource();
  const validate = useValidateSource();
  const preview = useSourcePreview();
  /** The source just created — the preview step needs its id. */
  const [created, setCreated] = useState<{ source_id: string; name?: string } | null>(null);
  /** The last create refusal (4xx/5xx), split per field (B1). */
  const [createErrors, setCreateErrors] = useState<ApiFieldErrors | null>(null);
  const connErrors = createErrors ? connectionConfigErrors(createErrors) : {};
  // Field errors the form can't place go to the summary instead.
  const unplacedConnErrors =
    selectedFamily && selectedType && formShowsFieldErrors(selectedFamily, selectedType) ? {} : connErrors;
  const otherFieldErrors = Object.entries(createErrors?.fields ?? {}).filter(
    ([path]) => !path.startsWith('connection_config.') && !['name', 'collection_id', 'sync_mode'].includes(path),
  );

  function payload(): Partial<SourceConfig> {
    return {
      name: sourceName,
      family: selectedFamily ?? undefined,
      source_type: selectedType ?? undefined,
      connection_config: connConfig,
      sync_mode: syncMode,
      collection_id: collectionId,
    };
  }

  function handleSubmit() {
    if (!selectedFamily || !selectedType || !sourceName.trim()) return;
    setCreateErrors(null);
    create.mutate(payload(), {
      onSuccess: (data?: Partial<SourceConfig>) => {
        onCreated?.();
        // Preview (POST /sources/{id}/preview) needs the saved source's id.
        if (data?.source_id) {
          setCreated({ source_id: data.source_id, name: data.name });
          setStep('preview');
        } else {
          onClose();
        }
      },
      onError: (e: unknown) => setCreateErrors(parseApiFieldErrors(e)),
    });
  }

  /** Test connection: POST /sources/validate?check_connection=true — nothing is saved. */
  function handleTest() {
    if (!selectedFamily || !selectedType) return;
    setCreateErrors(null);
    validate.mutate({ ...payload(), name: sourceName.trim() || `${selectedType} source` }, {
      onError: (e: unknown) => {
        const parsed = parseApiFieldErrors(e);
        if (Object.keys(parsed.fields).length) setCreateErrors({ ...parsed, general: null });
      },
    });
  }

  const steps: Step[] = ['family', 'type', 'configure', 'preview'];
  const stepIdx = steps.indexOf(step);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label="Add knowledge source">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative w-full max-w-3xl rounded-xl bg-background shadow-xl flex flex-col max-h-[90vh] overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          {/* Breadcrumb steps */}
          <div className="flex items-center gap-2 text-sm">
            {steps.map((s, i) => (
              <span key={s} className={`flex items-center gap-1 ${step === s ? 'text-foreground font-medium' : 'text-muted-foreground'}`}>
                {i > 0 && <ChevronRight className="h-3 w-3" />}
                <span className={`w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold ${
                  i < stepIdx ? 'bg-primary text-primary-foreground' :
                  i === stepIdx ? 'bg-primary text-primary-foreground' :
                  'bg-muted text-muted-foreground'
                }`}>{i + 1}</span>
                {s.charAt(0).toUpperCase() + s.slice(1)}
              </span>
            ))}
          </div>
          <button onClick={onClose} aria-label="Close" className="rounded-md p-2 hover:bg-muted transition-colors">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5">
          <AnimatePresence mode="wait">
            <motion.div
              key={step}
              initial={false}
              animate={{ x: 0, opacity: 1 }}
              exit={{ x: -60, opacity: 0 }}
              transition={SPRING_PAGE}
            >
              {/* Step 1: Pick family */}
              {step === 'family' && (
                <div>
                  <h2 className="text-base font-semibold mb-4">Choose a source family</h2>
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                    {ALL_FAMILIES.map(f => {
                      const cfg = FAMILY_CONFIG[f];
                      return (
                        <button
                          key={f}
                          onClick={() => { setSelectedFamily(f); setStep('type'); }}
                          className="rounded-xl border border-border p-4 text-left hover:border-primary hover:bg-primary/5 transition-[color,background-color,border-color,opacity,box-shadow,transform] group"
                        >
                          <div className={`font-medium text-sm group-hover:text-primary`}>{cfg.label}</div>
                          <div className="text-xs text-muted-foreground mt-1">{cfg.description}</div>
                          <div className="text-xs text-muted-foreground mt-2">{SOURCE_TYPES_BY_FAMILY[f].length} types</div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Step 2: Pick type */}
              {step === 'type' && selectedFamily && (
                <div>
                  <button onClick={() => setStep('family')} className="text-sm text-muted-foreground hover:text-foreground mb-4 flex items-center gap-1">← Back</button>
                  <h2 className="text-base font-semibold mb-4">{FAMILY_CONFIG[selectedFamily].label}</h2>
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                    {SOURCE_TYPES_BY_FAMILY[selectedFamily].map(t => (
                      <button
                        key={t}
                        onClick={() => { setSelectedType(t); setStep('configure'); }}
                        className="rounded-lg border border-border px-3 py-2 text-left text-sm font-mono hover:border-primary hover:bg-primary/5 transition-[color,background-color,border-color,opacity,box-shadow,transform]"
                      >
                        {t}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* Step 3: Configure */}
              {step === 'configure' && selectedFamily && selectedType && (
                <div>
                  <button onClick={() => setStep('type')} className="text-sm text-muted-foreground hover:text-foreground mb-4 flex items-center gap-1">← Back</button>
                  <h2 className="text-base font-semibold mb-4">Configure <code className="font-mono bg-muted rounded px-1.5 py-0.5 text-sm">{selectedType}</code></h2>

                  <div className="space-y-4">
                    <Field label="Source Name *" error={createErrors?.fields.name}>
                      <input type="text" value={sourceName} onChange={e => setSourceName(e.target.value)}
                        aria-invalid={createErrors?.fields.name ? true : undefined}
                        placeholder={`My ${selectedType} source`} className={inputCls} />
                    </Field>

                    <FamilyFormRouter family={selectedFamily} sourceType={selectedType} value={connConfig} onChange={setConnConfig} errors={connErrors} />

                    <Field label="Target Collection ID" hint="Leave blank to use the default collection" error={createErrors?.fields.collection_id}>
                      <input type="text" value={collectionId} onChange={e => setCollectionId(e.target.value)}
                        aria-invalid={createErrors?.fields.collection_id ? true : undefined}
                        placeholder="col-abc123" className={`${inputCls} font-mono`} />
                    </Field>

                    <Field label="Sync Mode" error={createErrors?.fields.sync_mode}>
                      <select value={syncMode} onChange={e => setSyncMode(e.target.value as typeof syncMode)} className={inputCls}>
                        <option value="incremental">Incremental (default)</option>
                        <option value="full">Full re-index</option>
                        <option value="streaming">Streaming (real-time)</option>
                      </select>
                    </Field>
                  </div>
                </div>
              )}

              {/* Step 4: Preview the created source (dry run, nothing is indexed) */}
              {step === 'preview' && created && (
                <div className="space-y-4">
                  <h2 className="text-base font-semibold">Source created{created.name ? `: ${created.name}` : ''}</h2>
                  <p className="text-sm text-muted-foreground">
                    Preview parses and chunks the first few documents without embedding or indexing anything.
                  </p>
                  <button
                    onClick={() => preview.mutate(created.source_id)}
                    disabled={preview.isPending}
                    className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-muted transition-colors disabled:opacity-50"
                  >
                    {preview.isPending ? 'Previewing…' : 'Preview documents'}
                  </button>
                  <PreviewResult preview={preview} />
                </div>
              )}
            </motion.div>
          </AnimatePresence>
        </div>

        {/* Footer */}
        {step === 'configure' && createErrors && (createErrors.general || otherFieldErrors.length > 0 || Object.keys(unplacedConnErrors).length > 0) && (
          <div role="alert" className="border-t border-border bg-red-50 px-5 py-3 text-sm text-red-700 dark:bg-red-900/20 dark:text-red-300">
            <p className="font-medium">Could not create the source</p>
            {createErrors.general && <FriendlyErrorMessage className="text-xs" error={createErrors.general} />}
            {[...otherFieldErrors, ...Object.entries(unplacedConnErrors)].map(([k, msg]) => (
              <p key={k} className="text-xs"><code className="font-mono">{k}</code>: {msg}</p>
            ))}
          </div>
        )}
        {step === 'configure' && (validate.isPending || validate.data || validate.error) && (
          <div data-testid="connection-test-result" className="border-t border-border px-5 py-3 text-sm">
            <ConnectionTestResult pending={validate.isPending} data={validate.data} error={validate.error} />
          </div>
        )}
        {step === 'preview' && (
          <div className="border-t border-border px-5 py-4 flex gap-2 justify-end">
            <button onClick={onClose} className="rounded-lg bg-primary text-primary-foreground px-4 py-2 text-sm font-medium hover:bg-primary/90 transition-colors">Done</button>
          </div>
        )}
        {step === 'configure' && (
          <div className="border-t border-border px-5 py-4 flex gap-2 justify-end">
            <button
              onClick={handleTest}
              disabled={validate.isPending}
              className="mr-auto rounded-lg border border-border px-4 py-2 text-sm hover:bg-muted transition-colors disabled:opacity-50"
            >
              {validate.isPending ? 'Testing…' : 'Test connection'}
            </button>
            <button onClick={onClose} className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-muted transition-colors">Cancel</button>
            <button
              onClick={handleSubmit}
              disabled={create.isPending || !sourceName.trim()}
              className="rounded-lg bg-primary text-primary-foreground px-4 py-2 text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
            >
              {create.isPending ? 'Creating…' : 'Create Source'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function ConnectionTestResult({ pending, data, error }: {
  pending: boolean; data: SourceValidation | undefined; error: unknown;
}) {
  if (pending) return <p className="text-muted-foreground">Testing the connection…</p>;
  if (error) {
    const parsed = parseApiFieldErrors(error);
    return parsed.general || !Object.keys(parsed.fields).length
      ? <FriendlyErrorMessage role="alert" className="text-sm text-red-600" prefix="Test failed: " error={parsed.general ?? error} />
      : <p className="text-red-600">Fix the highlighted fields and test again.</p>;
  }
  if (!data) return null;
  if (data.valid) {
    return data.connection
      ? <p className="text-emerald-600">● Connection OK{data.connection.latency_ms != null ? ` · ${Math.round(data.connection.latency_ms)} ms` : ''}</p>
      : <p className="text-emerald-600">Configuration valid (the connection was not checked).</p>;
  }
  // Prefer the connection's own error (the errors list wraps it in a prefix).
  const reasons = data.connection?.error ? [data.connection.error, ...data.errors.filter(e => !e.startsWith('Connection check failed'))] : data.errors;
  return (
    <div className="space-y-1">
      {(reasons.length ? reasons : ['The configuration is not valid.']).map((r, i) => (
        <FriendlyErrorMessage key={i} className="text-sm text-red-600" prefix="✕ " error={r} />
      ))}
    </div>
  );
}

function PreviewResult({ preview }: { preview: ReturnType<typeof useSourcePreview> }) {
  if (preview.error) {
    return <FriendlyErrorMessage role="alert" className="text-sm text-red-600" prefix="Preview failed: " error={preview.error} />;
  }
  const data = preview.data;
  if (!data) return null;
  // The endpoint answers HTTP 200 with {error} when the connector fails.
  if (data.error) {
    return <FriendlyErrorMessage role="alert" className="text-sm text-red-600" prefix="Preview failed: " error={data.error} />;
  }
  const sample = data.sample ?? [];
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium">{data.docs_previewed} documents previewed</p>
      {sample.length > 0 && (
        <table className="w-full text-xs">
          <thead><tr className="text-left text-muted-foreground"><th className="py-1">Document</th><th>Status</th><th>Chunks</th><th>Tokens</th></tr></thead>
          <tbody>
            {sample.map(d => (
              <tr key={d.doc_id} className="border-t border-border/50">
                <td className="py-1 font-mono break-all">{d.doc_id}</td>
                <td>{d.status}</td>
                <td>{d.chunks_would_create ?? '—'}</td>
                <td>{d.tokens_estimate ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Field({ label, hint, error, children }: { label: string; hint?: string; error?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-sm font-medium mb-1">{label}</label>
      {hint && <p className="text-xs text-muted-foreground mb-1.5">{hint}</p>}
      {children}
      <FieldError error={error} />
    </div>
  );
}
const inputCls = 'w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring';
