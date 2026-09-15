/**
 * ChatArtifactCard — a downloadable file card rendered from an
 * `artifact_created` SSE event. Downloads via the backend
 * GET /chat/artifacts/{id}/download endpoint, and (optionally) opens the full
 * artifact in the ChatArtifactPanel side editor.
 */

import { type JSX } from 'react';
import { FileText, Download, Maximize2 } from 'lucide-react';
import { chatApi } from '@/lib/api/chat';

export interface ArtifactCardData {
  artifactId: string;
  title: string;
  language?: string;
}

interface Props {
  artifact: ArtifactCardData;
  onOpen?: (artifactId: string) => void;
}

export function ChatArtifactCard({ artifact, onOpen }: Props): JSX.Element {
  const { artifactId, title, language } = artifact;

  return (
    <div className="flex items-center gap-3 rounded-xl border border-indigo-200 dark:border-indigo-800 bg-indigo-50 dark:bg-indigo-950/50 px-3 py-2">
      <FileText className="h-5 w-5 shrink-0 text-indigo-500" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs font-medium text-indigo-700 dark:text-indigo-300">{title}</p>
        {language && (
          <p className="truncate text-[11px] text-indigo-500/70 dark:text-indigo-400/70">{language}</p>
        )}
      </div>
      <div className="flex items-center gap-1 shrink-0">
        {onOpen && (
          <button
            type="button"
            onClick={() => onOpen(artifactId)}
            aria-label={`Open ${title}`}
            className="rounded-lg p-1.5 text-indigo-600 hover:bg-indigo-100 dark:text-indigo-300 dark:hover:bg-indigo-900"
          >
            <Maximize2 className="h-4 w-4" />
          </button>
        )}
        <a
          href={chatApi.artifactDownloadUrl(artifactId)}
          download={title}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Download ${title}`}
          className="rounded-lg p-1.5 text-indigo-600 hover:bg-indigo-100 dark:text-indigo-300 dark:hover:bg-indigo-900"
        >
          <Download className="h-4 w-4" />
        </a>
      </div>
    </div>
  );
}
