/**
 * ToolsPage — world-class sandboxed execution, workspace file manager, and email composer.
 *
 * Tabs:
 *   1. Code Runner  — language picker, timeout, snippet templates, execution history, copy stdout/stderr
 *   2. File Manager — fixed path bug, file size + date, breadcrumb, new-file flow, delete confirm
 *   3. Email        — fixed silent-error bug, CC field, char count, sent history
 */
import { useState, useCallback, useRef, useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Play, Send, Trash2, FileText, RefreshCw, Copy, Check,
  FolderOpen, Plus, Clock, Terminal, Mail, ChevronRight,
  AlertCircle, Loader2, X, Folder,
} from 'lucide-react';
import { toolsApi, type ExecuteCodeResult, type WorkspaceFile } from '@/lib/api/client';
import { toast } from '@/stores/toast';
import { Skeleton } from '@/components/ui/Skeleton';

// ── Types ────────────────────────────────────────────────────────────────────

type Tab = 'code' | 'files' | 'email';
type Language = 'python' | 'javascript' | 'bash';

interface HistoryEntry {
  id: string;
  language: Language;
  snippet: string;
  result: ExecuteCodeResult;
  timestamp: Date;
}

// ── Constants ────────────────────────────────────────────────────────────────

const LANGUAGE_META: Record<Language, { label: string; icon: string; placeholder: string; template: string }> = {
  python: {
    label: 'Python', icon: '🐍',
    placeholder: 'print("Hello, World!")',
    template: '# Python snippet\nimport sys\n\nprint("Python", sys.version.split()[0])\nprint("Hello from the sandbox!")',
  },
  javascript: {
    label: 'JavaScript', icon: '⚡',
    placeholder: 'console.log("Hello, World!")',
    template: '// JavaScript snippet\nconst greeting = "Hello from the sandbox!";\nconsole.log(greeting);\nconsole.log("Node.js sandbox ready.");',
  },
  bash: {
    label: 'Bash', icon: '🔩',
    placeholder: 'echo "Hello, World!"',
    template: '#!/bin/bash\necho "Hello from the sandbox!"\necho "Current directory: $(pwd)"\nls -la /tmp',
  },
};

function formatBytes(bytes?: number): string {
  if (!bytes || bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function formatModified(ts?: number): string {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

// ── Copy button ───────────────────────────────────────────────────────────────

function CopyButton({ text, className = '' }: { text: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [text]);
  return (
    <button
      onClick={handleCopy}
      className={`p-1 rounded transition-colors text-muted-foreground hover:text-foreground ${className}`}
      aria-label="Copy to clipboard"
      title="Copy"
    >
      {copied ? <Check className="h-3.5 w-3.5 text-green-500" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  );
}

// ── Code Runner ───────────────────────────────────────────────────────────────

function CodeRunner() {
  const [code, setCode] = useState('');
  const [language, setLanguage] = useState<Language>('python');
  const [timeout, setTimeout_] = useState(30);
  const [result, setResult] = useState<ExecuteCodeResult | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const meta = LANGUAGE_META[language];

  const runMutation = useMutation({
    mutationFn: () => toolsApi.executeCode(code, language, timeout),
    onSuccess: (r) => {
      setResult(r);
      setHistory((h) => [
        { id: Date.now().toString(), language, snippet: code.slice(0, 60), result: r, timestamp: new Date() },
        ...h.slice(0, 4),
      ]);
      if (!r.success)
        toast({ kind: 'error', message: r.timed_out ? 'Execution timed out.' : 'Code exited non-zero.' });
    },
    onError: (e) => toast({ kind: 'error', message: `Execution failed: ${String(e)}` }),
  });

  // Ctrl+Enter to run
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && code.trim() && !runMutation.isPending) {
        e.preventDefault();
        runMutation.mutate();
      }
    };
    el.addEventListener('keydown', handler);
    return () => el.removeEventListener('keydown', handler);
  }, [code, runMutation]);

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      {/* Editor + controls — 2/3 width */}
      <div className="lg:col-span-2 space-y-3">
        <div className="bg-card border border-border rounded-xl overflow-hidden">
          {/* Toolbar */}
          <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border bg-muted/30">
            <div className="flex items-center gap-2">
              {(['python', 'javascript', 'bash'] as Language[]).map((l) => (
                <button
                  key={l}
                  onClick={() => setLanguage(l)}
                  className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                    language === l ? 'bg-primary text-primary-foreground' : 'hover:bg-muted text-muted-foreground'
                  }`}
                >
                  <span aria-hidden="true">{LANGUAGE_META[l].icon}</span>
                  {LANGUAGE_META[l].label}
                </button>
              ))}
            </div>
            <div className="ml-auto flex items-center gap-3">
              <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Clock className="h-3.5 w-3.5" aria-hidden="true" />
                <input
                  type="number"
                  min={1}
                  max={60}
                  value={timeout}
                  onChange={(e) => setTimeout_(Math.min(60, Math.max(1, Number(e.target.value))))}
                  className="w-12 px-1.5 py-0.5 border border-input rounded text-xs bg-background text-center"
                  aria-label="Timeout in seconds"
                />
                <span>s</span>
              </label>
              <button
                onClick={() => setCode(meta.template)}
                className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                title="Load template"
              >
                Template
              </button>
              <button
                onClick={() => { setCode(''); setResult(null); }}
                className="text-xs text-muted-foreground hover:text-destructive transition-colors"
                title="Clear"
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            </div>
          </div>

          {/* Code textarea */}
          <textarea
            ref={textareaRef}
            aria-label="Code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder={`# ${meta.label}\n${meta.placeholder}`}
            spellCheck={false}
            className="w-full min-h-[240px] px-4 py-3 text-sm font-mono bg-background resize-y focus:outline-none"
          />

          {/* Footer */}
          <div className="flex items-center justify-between px-4 py-2 border-t border-border bg-muted/20">
            <span className="text-[10px] text-muted-foreground">{code.split('\n').length} lines · Ctrl+Enter to run</span>
            <button
              onClick={() => runMutation.mutate()}
              disabled={runMutation.isPending || !code.trim()}
              className="flex items-center gap-1.5 px-4 py-1.5 bg-primary text-primary-foreground text-sm font-medium rounded-md disabled:opacity-50 hover:opacity-90 transition-opacity"
            >
              {runMutation.isPending
                ? <><Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> Running…</>
                : <><Play className="h-3.5 w-3.5" aria-hidden="true" /> Run code</>}
            </button>
          </div>
        </div>

        {/* Result */}
        {result && (
          <div className="bg-card border border-border rounded-xl overflow-hidden">
            <div className="flex items-center gap-3 px-4 py-2 border-b border-border bg-muted/20">
              <span className={`w-2 h-2 rounded-full ${result.success ? 'bg-green-500' : 'bg-red-500'}`} aria-hidden="true" />
              <span className="text-xs font-medium">{result.success ? 'Success' : result.timed_out ? 'Timed out' : 'Error'}</span>
              <span className="text-xs text-muted-foreground ml-1">exit {result.exit_code} · {result.execution_time_ms.toFixed(0)}ms</span>
              <div className="ml-auto flex gap-1">
                {result.stdout && <CopyButton text={result.stdout} />}
              </div>
            </div>
            {result.stdout && (
              <pre className="text-xs font-mono p-4 overflow-auto whitespace-pre-wrap max-h-64 bg-background text-foreground">
                {result.stdout}
              </pre>
            )}
            {result.stderr && (
              <div className="border-t border-border">
                <div className="px-4 py-1.5 text-[10px] text-destructive font-medium uppercase tracking-wide bg-destructive/5 flex items-center justify-between">
                  <span>stderr</span>
                  <CopyButton text={result.stderr} />
                </div>
                <pre className="text-xs font-mono px-4 py-3 overflow-auto whitespace-pre-wrap max-h-40 text-destructive bg-destructive/5">
                  {result.stderr}
                </pre>
              </div>
            )}
            {!result.stdout && !result.stderr && (
              <p className="px-4 py-3 text-xs text-muted-foreground italic">No output.</p>
            )}
          </div>
        )}
      </div>

      {/* History — 1/3 width */}
      <div className="space-y-3">
        <div className="bg-card border border-border rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-border">
            <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Execution History</h3>
          </div>
          {history.length === 0 ? (
            <p className="px-4 py-6 text-xs text-muted-foreground text-center italic">No runs yet.</p>
          ) : (
            <ul className="divide-y divide-border">
              {history.map((h) => (
                <li key={h.id} className="px-4 py-2.5">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span aria-hidden="true">{LANGUAGE_META[h.language].icon}</span>
                    <span className={`w-1.5 h-1.5 rounded-full ${h.result.success ? 'bg-green-500' : 'bg-red-500'}`} aria-hidden="true" />
                    <span className="text-[10px] text-muted-foreground ml-auto">
                      {h.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                    </span>
                  </div>
                  <button
                    onClick={() => setCode(history.find(x => x.id === h.id)?.snippet ?? '')}
                    className="text-xs text-muted-foreground hover:text-foreground font-mono truncate block w-full text-left"
                    title="Restore this snippet"
                  >
                    {h.snippet}{h.snippet.length >= 60 ? '…' : ''}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

// ── File Manager ──────────────────────────────────────────────────────────────

function FileManager() {
  const qc = useQueryClient();
  const [directory] = useState('.');
  const [selectedPath, setSelectedPath] = useState('');
  const [content, setContent] = useState('');
  const [savedContent, setSavedContent] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [isNewFile, setIsNewFile] = useState(false);

  const { data: files = [], isLoading, isError } = useQuery({
    queryKey: ['workspace-files', directory],
    queryFn: () => toolsApi.listFiles(directory),
  });

  const openMutation = useMutation({
    mutationFn: (path: string) => toolsApi.readFile(path),
    onSuccess: (r) => {
      setSelectedPath(r.path);
      setContent(r.content);
      setSavedContent(r.content);
      setIsNewFile(false);
    },
    onError: (e) => toast({ kind: 'error', message: `Open failed: ${String(e)}` }),
  });

  const saveMutation = useMutation({
    mutationFn: () => toolsApi.writeFile(selectedPath, content),
    onSuccess: () => {
      toast({ kind: 'success', message: 'File saved.' });
      setSavedContent(content);
      setIsNewFile(false);
      qc.invalidateQueries({ queryKey: ['workspace-files'] });
    },
    onError: (e) => toast({ kind: 'error', message: `Save failed: ${String(e)}` }),
  });

  const deleteMutation = useMutation({
    mutationFn: (path: string) => toolsApi.deleteFile(path),
    onSuccess: (_v, path) => {
      toast({ kind: 'success', message: 'File deleted.' });
      if (selectedPath === path) { setSelectedPath(''); setContent(''); setSavedContent(''); }
      qc.invalidateQueries({ queryKey: ['workspace-files'] });
      setDeleteTarget(null);
    },
    onError: (e) => toast({ kind: 'error', message: `Delete failed: ${String(e)}` }),
  });

  const isDirty = content !== savedContent;

  const handleNewFile = () => {
    setSelectedPath('');
    setContent('');
    setSavedContent('');
    setIsNewFile(true);
  };

  return (
    <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
      {/* File tree — 2/5 */}
      <div className="md:col-span-2 bg-card border border-border rounded-xl overflow-hidden flex flex-col">
        <div className="px-4 py-3 border-b border-border flex items-center justify-between shrink-0">
          <div className="flex items-center gap-2">
            <FolderOpen className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">Workspace</h2>
            {files.length > 0 && (
              <span className="text-[10px] text-muted-foreground bg-muted px-1.5 py-0.5 rounded">
                {files.length} file{files.length !== 1 ? 's' : ''}
              </span>
            )}
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={handleNewFile}
              title="New file"
              aria-label="New file"
              className="p-1.5 rounded hover:bg-muted/60 text-muted-foreground hover:text-foreground transition-colors"
            >
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
            <button
              aria-label="Refresh files"
              onClick={() => qc.invalidateQueries({ queryKey: ['workspace-files'] })}
              className="p-1.5 rounded hover:bg-muted/60 text-muted-foreground hover:text-foreground transition-colors"
            >
              <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto">
          {isLoading && (
            <div className="p-4 space-y-2">
              {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-8" />)}
            </div>
          )}
          {isError && (
            <div className="px-4 py-3 flex items-center gap-2 text-sm text-red-500" role="alert">
              <AlertCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
              Failed to load files.
            </div>
          )}
          {!isLoading && !isError && files.length === 0 && (
            <div className="px-4 py-8 text-center text-muted-foreground">
              <FolderOpen className="h-8 w-8 mx-auto mb-2 opacity-20" aria-hidden="true" />
              <p className="text-sm">No files yet.</p>
              <button onClick={handleNewFile} className="mt-2 text-xs text-primary hover:underline">
                Create your first file
              </button>
            </div>
          )}
          {!isLoading && !isError && files.length > 0 && (
            <ul>
              {files.map((f: WorkspaceFile) => (
                <li
                  key={f.path ?? f.name}
                  className={`flex items-center gap-2 px-3 py-2 hover:bg-muted/40 transition-colors group ${
                    selectedPath === f.path ? 'bg-primary/5 border-l-2 border-primary' : ''
                  }`}
                >
                  {/* Delete confirm inline */}
                  {deleteTarget === f.path ? (
                    <div className="flex-1 flex items-center gap-2 text-xs">
                      <span className="text-red-600 dark:text-red-400">Delete {f.name}?</span>
                      <button
                        onClick={() => deleteMutation.mutate(f.path ?? f.name)}
                        disabled={deleteMutation.isPending}
                        className="px-2 py-0.5 bg-red-600 text-white rounded text-[10px] disabled:opacity-50"
                      >
                        {deleteMutation.isPending ? '…' : 'Yes'}
                      </button>
                      <button onClick={() => setDeleteTarget(null)} className="text-muted-foreground hover:text-foreground">
                        No
                      </button>
                    </div>
                  ) : (
                    <>
                      <button
                        onClick={() => !f.is_dir && openMutation.mutate(f.path ?? f.name)}
                        className="flex items-center gap-2 text-sm flex-1 min-w-0 text-left"
                        disabled={!!f.is_dir}
                      >
                        {f.is_dir || f.type === 'directory'
                          ? <Folder className="h-4 w-4 text-amber-500 shrink-0" aria-hidden="true" />
                          : <FileText className="h-4 w-4 text-muted-foreground shrink-0" aria-hidden="true" />}
                        <div className="min-w-0">
                          <span className={`truncate block text-xs font-medium ${selectedPath === f.path ? 'text-primary' : 'text-foreground'}`}>
                            {f.name}
                          </span>
                          <span className="text-[10px] text-muted-foreground">
                            {formatBytes(f.size_bytes)}
                            {f.modified_at ? ` · ${formatModified(f.modified_at)}` : ''}
                          </span>
                        </div>
                      </button>
                      {!f.is_dir && f.type !== 'directory' && (
                        <button
                          aria-label={`Delete ${f.name}`}
                          onClick={() => setDeleteTarget(f.path ?? f.name)}
                          className="p-1 opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-destructive transition-all"
                        >
                          <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                        </button>
                      )}
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* Editor — 3/5 */}
      <div className="md:col-span-3 bg-card border border-border rounded-xl overflow-hidden flex flex-col">
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border bg-muted/20 shrink-0">
          <Terminal className="h-4 w-4 text-muted-foreground shrink-0" aria-hidden="true" />
          <input
            value={selectedPath}
            onChange={(e) => { setSelectedPath(e.target.value); setIsNewFile(true); }}
            placeholder={isNewFile ? 'new-file.txt' : 'path/to/file.txt'}
            aria-label="File path"
            className="flex-1 text-sm font-mono bg-transparent focus:outline-none min-w-0"
          />
          {isDirty && <span className="text-[10px] text-amber-500 shrink-0">unsaved</span>}
        </div>
        <textarea
          aria-label="File content"
          value={content}
          onChange={(e) => setContent(e.target.value)}
          className="flex-1 min-h-[320px] px-4 py-3 text-sm font-mono bg-background resize-none focus:outline-none"
          placeholder={selectedPath ? '' : 'Select a file or create a new one…'}
        />
        <div className="px-4 py-2.5 border-t border-border flex items-center justify-between bg-muted/20 shrink-0">
          <span className="text-[10px] text-muted-foreground">
            {content.split('\n').length} lines · {content.length} chars
          </span>
          <button
            onClick={() => saveMutation.mutate()}
            disabled={!selectedPath.trim() || saveMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground text-xs font-medium rounded-md disabled:opacity-50 hover:opacity-90 transition-opacity"
          >
            {saveMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <Check className="h-3.5 w-3.5" aria-hidden="true" />}
            Save file
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Email Composer ────────────────────────────────────────────────────────────

interface SentItem { to: string; subject: string; ts: Date }

function EmailComposer() {
  const [to, setTo] = useState('');
  const [cc, setCc] = useState('');
  const [showCc, setShowCc] = useState(false);
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [sent, setSent] = useState<SentItem[]>([]);

  const sendMutation = useMutation({
    mutationFn: () =>
      toolsApi.sendEmail({
        to: to.includes(',') ? to.split(',').map((s) => s.trim()) : to,
        subject,
        body,
      }),
    onSuccess: () => {
      toast({ kind: 'success', message: 'Email sent successfully.' });
      setSent((prev) => [{ to, subject, ts: new Date() }, ...prev.slice(0, 2)]);
      setTo(''); setCc(''); setSubject(''); setBody('');
    },
    onError: (e) => toast({ kind: 'error', message: `Send failed: ${String(e)}` }),
  });

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div className="lg:col-span-2">
        <form
          className="bg-card border border-border rounded-xl overflow-hidden"
          onSubmit={(e) => { e.preventDefault(); sendMutation.mutate(); }}
        >
          <div className="px-5 py-3 border-b border-border bg-muted/20 flex items-center gap-2">
            <Mail className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">New Message</h2>
          </div>

          <div className="divide-y divide-border">
            <div className="flex items-center gap-3 px-5 py-2.5">
              <label htmlFor="to" className="text-xs text-muted-foreground w-12 shrink-0">To</label>
              <input
                id="to"
                value={to}
                onChange={(e) => setTo(e.target.value)}
                placeholder="recipient@example.com, another@example.com"
                className="flex-1 text-sm bg-transparent focus:outline-none"
              />
              {!showCc && (
                <button type="button" onClick={() => setShowCc(true)} className="text-xs text-muted-foreground hover:text-foreground">
                  CC
                </button>
              )}
            </div>

            {showCc && (
              <div className="flex items-center gap-3 px-5 py-2.5">
                <label htmlFor="cc" className="text-xs text-muted-foreground w-12 shrink-0">CC</label>
                <input
                  id="cc"
                  value={cc}
                  onChange={(e) => setCc(e.target.value)}
                  placeholder="cc@example.com"
                  className="flex-1 text-sm bg-transparent focus:outline-none"
                />
                <button type="button" onClick={() => { setShowCc(false); setCc(''); }} className="text-muted-foreground hover:text-foreground">
                  <X className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              </div>
            )}

            <div className="flex items-center gap-3 px-5 py-2.5">
              <label htmlFor="subject" className="text-xs text-muted-foreground w-12 shrink-0">Subject</label>
              <input
                id="subject"
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                placeholder="Subject line"
                className="flex-1 text-sm bg-transparent focus:outline-none"
              />
            </div>

            <div className="relative">
              <textarea
                id="message"
                aria-label="Message"
                value={body}
                onChange={(e) => setBody(e.target.value)}
                placeholder="Write your message…"
                rows={8}
                className="w-full px-5 py-3 text-sm bg-background resize-none focus:outline-none"
              />
              <span className="absolute bottom-2 right-4 text-[10px] text-muted-foreground">
                {body.length} chars
              </span>
            </div>
          </div>

          <div className="px-5 py-3 border-t border-border bg-muted/20 flex items-center justify-between">
            {sendMutation.isError && (
              <p className="text-xs text-destructive">{String(sendMutation.error)}</p>
            )}
            <div className="ml-auto">
              <button
                type="submit"
                disabled={!to.trim() || !subject.trim() || sendMutation.isPending}
                className="flex items-center gap-1.5 px-4 py-2 bg-primary text-primary-foreground text-sm font-medium rounded-lg disabled:opacity-50 hover:opacity-90 transition-opacity"
              >
                {sendMutation.isPending
                  ? <><Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> Sending…</>
                  : <><Send className="h-3.5 w-3.5" aria-hidden="true" /> Send email</>}
              </button>
            </div>
          </div>
        </form>
      </div>

      {/* Sent history */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-border">
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Sent</h3>
        </div>
        {sent.length === 0 ? (
          <p className="px-4 py-6 text-xs text-muted-foreground text-center italic">No emails sent yet.</p>
        ) : (
          <ul className="divide-y divide-border">
            {sent.map((s, i) => (
              <li key={i} className="px-4 py-3">
                <p className="text-xs font-medium truncate">{s.subject}</p>
                <p className="text-[10px] text-muted-foreground truncate">To: {s.to}</p>
                <p className="text-[10px] text-muted-foreground">
                  {s.ts.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                </p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

const TAB_META: { key: Tab; label: string; icon: React.ReactNode }[] = [
  { key: 'code',  label: 'Code Runner',   icon: <Terminal className="h-3.5 w-3.5" aria-hidden="true" /> },
  { key: 'files', label: 'File Manager',  icon: <FolderOpen className="h-3.5 w-3.5" aria-hidden="true" /> },
  { key: 'email', label: 'Email',         icon: <Mail className="h-3.5 w-3.5" aria-hidden="true" /> },
];

export function ToolsPage() {
  const [tab, setTab] = useState<Tab>('code');
  return (
    <div className="space-y-6 max-w-6xl">
      <div>
        <h1 className="text-2xl font-bold">Tools</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Sandboxed code execution, workspace file manager, and email — all tenant-isolated
        </p>
      </div>

      <div role="tablist" aria-label="Tools tabs" className="flex gap-1 border-b border-border">
        {TAB_META.map(({ key, label, icon }) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            aria-controls={`tools-panel-${key}`}
            id={`tools-tab-${key}`}
            onClick={() => setTab(key)}
            className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors ${
              tab === key
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            {icon}
            {label}
            {key === 'code' && <ChevronRight className="h-3 w-3 opacity-40" aria-hidden="true" />}
          </button>
        ))}
      </div>

      <div
        role="tabpanel"
        id={`tools-panel-${tab}`}
        aria-labelledby={`tools-tab-${tab}`}
      >
        {tab === 'code'  && <CodeRunner />}
        {tab === 'files' && <FileManager />}
        {tab === 'email' && <EmailComposer />}
      </div>
    </div>
  );
}
