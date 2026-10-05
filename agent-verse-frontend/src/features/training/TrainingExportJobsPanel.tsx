/**
 * Background (durable) training-data export jobs — OPS-37.
 *
 * Large exports run on a worker that writes the JSONL to object storage; this
 * panel starts one, polls the tenant's jobs while any is active, downloads a
 * finished file, and shows a failed job with the reason the server recorded.
 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, CheckCircle2, Clock, Download, Loader2, Server } from 'lucide-react';
import { ApiError, trainingApi, type TrainingExportJob } from '@/lib/api/client';
import { toast } from '@/stores/toast';

type PageFormat = 'openai' | 'anthropic' | 'llama' | 'sharegpt';

const JOBS_KEY = ['training-export-jobs'] as const;
const DEFAULT_JOB_LIMIT = 100_000;
const MAX_JOB_LIMIT = 1_000_000;

function errorText(err: unknown): string {
  if (err instanceof ApiError || err instanceof Error) return err.message;
  return 'Unknown error';
}

function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function isActive(job: TrainingExportJob): boolean {
  return job.status === 'queued' || job.status === 'running';
}

function JobStatus({ job }: { job: TrainingExportJob }) {
  switch (job.status) {
    case 'queued':
      return (
        <span className="flex items-center gap-1 text-xs text-muted-foreground">
          <Clock className="h-3.5 w-3.5" /> Queued
        </span>
      );
    case 'running':
      return (
        <span className="flex items-center gap-1 text-xs text-blue-600">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Running
        </span>
      );
    case 'complete':
      return (
        <span className="flex items-center gap-1 text-xs text-green-700">
          <CheckCircle2 className="h-3.5 w-3.5" />
          {(job.example_count ?? 0).toLocaleString('en-US')} examples
        </span>
      );
    case 'failed':
      return (
        <span role="alert" className="flex items-start gap-1 text-xs text-destructive">
          <AlertCircle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <span className="break-all">Failed: {job.error || 'no reason recorded'}</span>
        </span>
      );
    default:
      return <span className="text-xs text-muted-foreground">{String(job.status)}</span>;
  }
}

export function TrainingExportJobsPanel({
  format,
  minScore,
  pollMs = 3000,
}: {
  format: PageFormat;
  minScore: number;
  /** Poll interval while a job is queued or running. */
  pollMs?: number;
}) {
  const qc = useQueryClient();
  const [jobLimit, setJobLimit] = useState(DEFAULT_JOB_LIMIT);
  const [downloading, setDownloading] = useState<string | null>(null);
  const serverFormat = format === 'openai' || format === 'anthropic' ? format : null;

  const jobsQuery = useQuery({
    queryKey: JOBS_KEY,
    queryFn: () => trainingApi.listJobs(),
    refetchInterval: (query) =>
      (query.state.data?.jobs ?? []).some(isActive) ? pollMs : false,
  });
  const jobs = jobsQuery.data?.jobs ?? [];

  const startJob = useMutation({
    mutationFn: () => {
      if (!serverFormat) throw new Error('Background jobs export OpenAI or Anthropic JSONL.');
      return trainingApi.createJob({ format: serverFormat, minScore, limit: jobLimit });
    },
    onSuccess: () => {
      toast({ kind: 'success', message: 'Background export queued.' });
      void qc.invalidateQueries({ queryKey: JOBS_KEY });
    },
  });

  const download = async (job: TrainingExportJob) => {
    setDownloading(job.job_id);
    try {
      const { blob, filename } = await trainingApi.downloadJob(job.job_id);
      saveBlob(blob, filename);
    } catch (err) {
      toast({ kind: 'error', message: `Download failed: ${errorText(err)}` });
    } finally {
      setDownloading(null);
    }
  };

  return (
    <div data-testid="export-jobs" className="bg-card border border-border rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-border bg-muted/40">
        <span className="font-medium text-sm flex items-center gap-2">
          <Server className="h-4 w-4" /> Background exports
        </span>
        <p className="text-xs text-muted-foreground mt-1">
          For large exports: a worker writes the file to storage; download it here when it is ready.
        </p>
      </div>

      <div className="p-4 space-y-3">
        <div className="flex items-end gap-3 flex-wrap">
          <label className="text-xs text-muted-foreground flex flex-col gap-1">
            Job example limit
            <input
              type="number"
              min={1}
              max={MAX_JOB_LIMIT}
              value={jobLimit}
              onChange={(e) =>
                setJobLimit(Math.min(MAX_JOB_LIMIT, Math.max(1, Number(e.target.value) || 1)))
              }
              className="w-32 rounded-md border border-border bg-background px-2 py-1 text-sm"
            />
          </label>
          <button
            data-testid="btn-start-export-job"
            onClick={() => startJob.mutate()}
            disabled={!serverFormat || startJob.isPending}
            className="flex items-center gap-2 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-50"
          >
            {startJob.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Start background export
          </button>
        </div>
        {!serverFormat && (
          <p className="text-xs text-muted-foreground">
            Background jobs export OpenAI or Anthropic JSONL; pick one of those formats.
          </p>
        )}
        {startJob.isError && (
          <p
            data-testid="export-job-start-error"
            role="alert"
            className="text-xs text-destructive flex items-center gap-1"
          >
            <AlertCircle className="h-3.5 w-3.5" /> Could not start the export:{' '}
            {errorText(startJob.error)}
          </p>
        )}

        {jobsQuery.isError ? (
          <p
            data-testid="export-jobs-error"
            role="alert"
            className="text-sm text-destructive flex items-center gap-2"
          >
            <AlertCircle className="h-4 w-4" /> Background exports are unavailable:{' '}
            {errorText(jobsQuery.error)}
          </p>
        ) : jobsQuery.isPending ? (
          <p className="text-sm text-muted-foreground flex items-center gap-2">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading background exports…
          </p>
        ) : jobs.length === 0 ? (
          <p className="text-sm text-muted-foreground">No background exports yet.</p>
        ) : (
          <ul className="divide-y divide-border border border-border rounded-md">
            {jobs.map((job) => (
              <li
                key={job.job_id}
                data-testid={`export-job-${job.job_id}`}
                className="px-3 py-2 flex items-center gap-3 text-sm"
              >
                <span className="px-2 py-0.5 rounded text-xs font-medium uppercase bg-muted text-muted-foreground">
                  {job.format}
                </span>
                <div className="flex-1 min-w-0">
                  <JobStatus job={job} />
                  <p className="text-xs text-muted-foreground mt-0.5">
                    score ≥ {(job.min_score ?? 0).toFixed(2)} · up to{' '}
                    {job.limit.toLocaleString('en-US')}
                    {job.created_at && ` · ${new Date(job.created_at).toLocaleString()}`}
                  </p>
                </div>
                {job.status === 'complete' && job.has_file && (
                  <button
                    onClick={() => void download(job)}
                    disabled={downloading === job.job_id}
                    aria-label={`Download export ${job.job_id}`}
                    className="flex items-center gap-1 text-xs rounded-md border border-border px-2 py-1 hover:bg-muted disabled:opacity-50"
                  >
                    {downloading === job.job_id ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Download className="h-3.5 w-3.5" />
                    )}
                    Download
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
