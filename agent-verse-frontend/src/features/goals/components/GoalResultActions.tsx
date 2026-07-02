import { Copy, Download, FileJson, FileText, Printer, RotateCcw } from 'lucide-react';
import { artifactToCsv, artifactToMarkdown, type ResultArtifact } from '../resultArtifact';
import { openPrintView } from './GoalPrintView';

function downloadFile(name: string, content: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.style.display = 'none';

  try {
    document.body.appendChild(link);
    link.click();
  } finally {
    if (link.parentNode) {
      document.body.removeChild(link);
    }
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}

function fallbackCopyText(text: string) {
  const textArea = document.createElement('textarea');
  textArea.value = text;
  textArea.setAttribute('readonly', '');
  textArea.style.position = 'fixed';
  textArea.style.opacity = '0';

  try {
    document.body.appendChild(textArea);
    textArea.select();
    document.execCommand('copy');
  } catch {
    // Copy support varies by browser context; failures should not break the page.
  } finally {
    textArea.remove();
  }
}

async function copyText(text: string) {
  try {
    const writeText = navigator.clipboard?.writeText;
    if (typeof writeText === 'function') {
      await writeText.call(navigator.clipboard, text);
      return;
    }
  } catch {
    // Fall back below when clipboard permission is denied or unavailable.
  }

  fallbackCopyText(text);
}

const actionClass =
  'inline-flex items-center justify-center gap-2 rounded-lg border border-border bg-background px-3 py-2 text-sm font-medium text-foreground shadow-sm transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2';

export function GoalResultActions({
  artifact,
  onRerun,
  goal = '',
}: {
  artifact: ResultArtifact;
  onRerun: () => void;
  goal?: string;
}) {
  const hasTable = artifact.tables.length > 0;
  const canDownloadJson = artifact.downloads.includes('json');
  const canDownloadCsv = artifact.downloads.includes('csv') && hasTable;
  const canDownloadMarkdown = artifact.downloads.includes('markdown');

  const copySummary = async () => {
    await copyText(artifact.summary);
  };

  return (
    <div className="flex flex-wrap gap-2">
      <button type="button" onClick={copySummary} className={actionClass}>
        <Copy className="h-4 w-4" aria-hidden="true" />
        Copy summary
      </button>
      {canDownloadJson && (
        <button
          type="button"
          onClick={() =>
            downloadFile('goal-result.json', JSON.stringify(artifact, null, 2), 'application/json')
          }
          className={actionClass}
        >
          <FileJson className="h-4 w-4" aria-hidden="true" />
          Download JSON
        </button>
      )}
      {canDownloadCsv && (
        <button
          type="button"
          onClick={() => downloadFile('goal-result.csv', artifactToCsv(artifact), 'text/csv')}
          className={actionClass}
        >
          <Download className="h-4 w-4" aria-hidden="true" />
          Download CSV
        </button>
      )}
      {canDownloadMarkdown && (
        <button
          type="button"
          onClick={() => downloadFile('goal-result.md', artifactToMarkdown(artifact), 'text/markdown')}
          className={actionClass}
        >
          <FileText className="h-4 w-4" aria-hidden="true" />
          Download Markdown
        </button>
      )}
      <button type="button" onClick={() => openPrintView(artifact, goal)} className={actionClass}>
        <Printer className="h-4 w-4" aria-hidden="true" />
        Print / PDF
      </button>
      <button
        type="button"
        onClick={onRerun}
        className="inline-flex items-center justify-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      >
        <RotateCcw className="h-4 w-4" aria-hidden="true" />
        Rerun goal
      </button>
    </div>
  );
}
