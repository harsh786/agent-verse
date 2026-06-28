import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Play, Send, Trash2, FileText, RefreshCw } from 'lucide-react';
import { toolsApi, type ExecuteCodeResult, type WorkspaceFile } from '@/lib/api/client';
import { toast } from '@/stores/toast';
import { StatusBadge } from '@/components/ui/StatusBadge';

type Tab = 'code' | 'files' | 'email';
const LANGUAGES = ['python', 'javascript', 'bash'] as const;

export function ToolsPage() {
  const [tab, setTab] = useState<Tab>('code');
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Tools</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Sandboxed code execution, workspace files, and email — governed actions
        </p>
      </div>
      <div role="tablist" className="flex gap-1 border-b border-border">
        {(['code', 'files', 'email'] as Tab[]).map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium capitalize border-b-2 -mb-px ${
              tab === t
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            {t === 'code' ? 'Code Runner' : t === 'files' ? 'File Manager' : 'Email'}
          </button>
        ))}
      </div>
      {tab === 'code' && <CodeRunner />}
      {tab === 'files' && <FileManager />}
      {tab === 'email' && <EmailComposer />}
    </div>
  );
}

function CodeRunner() {
  const [code, setCode] = useState('');
  const [language, setLanguage] = useState<(typeof LANGUAGES)[number]>('python');
  const [result, setResult] = useState<ExecuteCodeResult | null>(null);

  const runMutation = useMutation({
    mutationFn: () => toolsApi.executeCode(code, language, 30),
    onSuccess: (r) => {
      setResult(r);
      if (!r.success)
        toast({ kind: 'error', message: r.timed_out ? 'Execution timed out.' : 'Code exited non-zero.' });
    },
  });

  return (
    <div className="bg-card border border-border rounded-xl p-4 space-y-3">
      <div className="flex items-center gap-2">
        <label htmlFor="lang" className="text-sm text-muted-foreground">
          Language
        </label>
        <select
          id="lang"
          value={language}
          onChange={(e) => setLanguage(e.target.value as (typeof LANGUAGES)[number])}
          className="px-2 py-1 border border-border rounded-md text-sm bg-background"
        >
          {LANGUAGES.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
      </div>
      <textarea
        aria-label="Code"
        value={code}
        onChange={(e) => setCode(e.target.value)}
        placeholder="Enter code to execute in a sandboxed container…"
        className="w-full h-48 px-3 py-2 border border-border rounded-md text-sm font-mono bg-background"
      />
      <button
        onClick={() => runMutation.mutate()}
        disabled={runMutation.isPending || !code.trim()}
        className="flex items-center gap-1.5 px-3 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50"
      >
        <Play className="h-4 w-4" /> Run code
      </button>
      {result && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-sm">
            <StatusBadge status={result.success ? 'success' : 'failed'} />
            <span className="text-muted-foreground">
              exit {result.exit_code} · {result.execution_time_ms.toFixed(0)}ms
            </span>
          </div>
          {result.stdout && (
            <pre className="text-xs font-mono bg-muted rounded-md p-3 overflow-auto whitespace-pre-wrap">
              {result.stdout}
            </pre>
          )}
          {result.stderr && (
            <pre className="text-xs font-mono bg-destructive/10 text-destructive rounded-md p-3 overflow-auto whitespace-pre-wrap">
              {result.stderr}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

function FileManager() {
  const qc = useQueryClient();
  const [directory] = useState('.');
  const [selectedPath, setSelectedPath] = useState('');
  const [content, setContent] = useState('');

  const { data: files = [], isLoading, isError } = useQuery({
    queryKey: ['workspace-files', directory],
    queryFn: () => toolsApi.listFiles(directory),
  });

  const openMutation = useMutation({
    mutationFn: (path: string) => toolsApi.readFile(path),
    onSuccess: (r) => {
      setSelectedPath(r.path);
      setContent(r.content);
    },
  });
  const saveMutation = useMutation({
    mutationFn: () => toolsApi.writeFile(selectedPath, content),
    onSuccess: () => {
      toast({ kind: 'success', message: 'File saved.' });
      qc.invalidateQueries({ queryKey: ['workspace-files'] });
    },
  });
  const deleteMutation = useMutation({
    mutationFn: (path: string) => toolsApi.deleteFile(path),
    onSuccess: () => {
      toast({ kind: 'success', message: 'File deleted.' });
      qc.invalidateQueries({ queryKey: ['workspace-files'] });
    },
  });

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-5 py-3 border-b border-border flex items-center justify-between">
          <h2 className="font-semibold text-sm">Workspace</h2>
          <button
            aria-label="Refresh files"
            onClick={() => qc.invalidateQueries({ queryKey: ['workspace-files'] })}
            className="text-muted-foreground hover:text-foreground"
          >
            <RefreshCw className="h-4 w-4" />
          </button>
        </div>
        {isLoading && (
          <p className="px-5 py-4 text-sm text-muted-foreground">Loading…</p>
        )}
        {isError && (
          <div className="px-5 py-4 text-sm text-red-500" role="alert">
            Failed to load files. Check your connection and try again.
          </div>
        )}
        {!isLoading && !isError && files.length === 0 && (
          <p className="px-5 py-4 text-sm text-muted-foreground">No files in workspace.</p>
        )}
        {!isLoading && !isError && files.length > 0 && (
          <ul className="divide-y divide-border">
            {files.map((f: WorkspaceFile) => (
              <li key={f.path} className="px-5 py-2 flex items-center justify-between gap-2">
                <button
                  onClick={() => openMutation.mutate(f.path)}
                  className="flex items-center gap-2 text-sm hover:text-primary min-w-0"
                >
                  <FileText className="h-4 w-4 flex-shrink-0" />
                  <span className="truncate">{f.name}</span>
                </button>
                <button
                  aria-label={`Delete ${f.name}`}
                  onClick={() => deleteMutation.mutate(f.path)}
                  className="text-muted-foreground hover:text-destructive flex-shrink-0"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="bg-card border border-border rounded-xl p-4 space-y-3">
        <input
          value={selectedPath}
          onChange={(e) => setSelectedPath(e.target.value)}
          placeholder="path/to/file.txt"
          aria-label="File path"
          className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background"
        />
        <textarea
          aria-label="File content"
          value={content}
          onChange={(e) => setContent(e.target.value)}
          className="w-full h-64 px-3 py-2 border border-border rounded-md text-sm font-mono bg-background"
        />
        <button
          onClick={() => saveMutation.mutate()}
          disabled={!selectedPath.trim() || saveMutation.isPending}
          className="px-3 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50"
        >
          Save file
        </button>
      </div>
    </div>
  );
}

function EmailComposer() {
  const [to, setTo] = useState('');
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');

  const sendMutation = useMutation({
    mutationFn: () => toolsApi.sendEmail({ to, subject, body }),
    onSuccess: () => {
      toast({ kind: 'success', message: 'Email sent.' });
      setTo('');
      setSubject('');
      setBody('');
    },
  });

  return (
    <form
      className="bg-card border border-border rounded-xl p-4 space-y-3 max-w-xl"
      onSubmit={(e) => {
        e.preventDefault();
        sendMutation.mutate();
      }}
    >
      <div>
        <label htmlFor="to" className="text-sm text-muted-foreground">
          To
        </label>
        <input
          id="to"
          value={to}
          onChange={(e) => setTo(e.target.value)}
          className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background"
        />
      </div>
      <div>
        <label htmlFor="subject" className="text-sm text-muted-foreground">
          Subject
        </label>
        <input
          id="subject"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background"
        />
      </div>
      <div>
        <label htmlFor="message" className="text-sm text-muted-foreground">
          Message
        </label>
        <textarea
          id="message"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          className="w-full h-40 px-3 py-2 border border-border rounded-md text-sm bg-background"
        />
      </div>
      <button
        type="submit"
        disabled={!to.trim() || !subject.trim() || sendMutation.isPending}
        className="flex items-center gap-1.5 px-3 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50"
      >
        <Send className="h-4 w-4" /> Send email
      </button>
    </form>
  );
}
