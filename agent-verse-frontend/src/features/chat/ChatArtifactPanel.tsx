/**
 * ChatArtifactPanel — Monaco-style code editor side panel for artifacts.
 */

import { useState, type JSX } from 'react';
import { X, Copy, Download, Check } from 'lucide-react';
import type { ChatArtifact } from './types/chat.types';

interface Props {
  artifact: ChatArtifact | null;
  onClose: () => void;
  onSave?: (content: string) => void;
}

export function ChatArtifactPanel({ artifact, onClose, onSave }: Props): JSX.Element | null {
  const [content, setContent] = useState(artifact?.content ?? '');
  const [copied, setCopied] = useState(false);

  if (!artifact) return null;

  const handleCopy = async () => {
    await navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleDownload = () => {
    const blob = new Blob([content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = artifact.title;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <aside
      className="flex flex-col w-[480px] border-l border-white/[0.08] dark:border-gray-700 bg-[#0F1826] dark:bg-gray-900 h-full"
      aria-label="Artifact panel"
    >
      {/* Header */}
      <div className="flex items-center px-4 py-3 border-b border-white/[0.08] dark:border-gray-700">
        <div className="flex-1">
          <p className="text-sm font-medium text-[#F0F6FF] dark:text-gray-200 truncate">{artifact.title}</p>
          <p className="text-xs text-[#A0B4CC]">{artifact.language}</p>
        </div>
        <div className="flex items-center gap-1">
          <button
            className="p-1.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800"
            onClick={handleCopy}
            aria-label="Copy artifact content"
          >
            {copied ? <Check className="w-4 h-4 text-green-500" /> : <Copy className="w-4 h-4 text-[#A0B4CC]" />}
          </button>
          <button
            className="p-1.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800"
            onClick={handleDownload}
            aria-label="Download artifact"
          >
            <Download className="w-4 h-4 text-[#A0B4CC]" />
          </button>
          <button
            className="p-1.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800"
            onClick={onClose}
            aria-label="Close artifact panel"
          >
            <X className="w-4 h-4 text-[#A0B4CC]" />
          </button>
        </div>
      </div>

      {/* Editor */}
      <textarea
        className="flex-1 font-mono text-xs p-4 bg-[#0A0F1A] dark:bg-gray-950 text-[#F0F6FF] dark:text-gray-200 resize-none outline-none border-0"
        value={content}
        onChange={(e) => setContent(e.target.value)}
        aria-label="Artifact editor"
        spellCheck={false}
      />

      {/* Footer */}
      {onSave && (
        <div className="px-4 py-2 border-t border-white/[0.08] dark:border-gray-700">
          <button
            className="w-full py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white text-xs rounded-lg font-medium transition-colors"
            onClick={() => onSave(content)}
          >
            Save changes
          </button>
        </div>
      )}
    </aside>
  );
}
