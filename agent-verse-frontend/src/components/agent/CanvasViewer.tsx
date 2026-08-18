/**
 * CanvasViewer — renders agent-generated canvas content:
 * supports markdown, code, HTML preview, JSON, and images.
 *
 * Skills: frontend-design, emil-design-eng, impeccable-ui, web-guidelines
 */
import { useState, useRef } from 'react';
import { motion } from 'framer-motion';
import { Code2, FileText, Image, Maximize2, Minimize2, Copy, Check } from 'lucide-react';
import { SPRING_FAST } from '@/components/ui/JARVISPageShell';

export type CanvasContentType = 'markdown' | 'code' | 'html' | 'json' | 'image' | 'text';

interface CanvasViewerProps {
  content: string;
  contentType?: CanvasContentType;
  language?: string;      // for code blocks
  title?: string;
  className?: string;
}

function detectType(content: string): CanvasContentType {
  const trimmed = content.trimStart();
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) return 'json';
  if (trimmed.startsWith('<')) return 'html';
  if (trimmed.startsWith('```') || /^(import|export|function|const|class)\s/.test(trimmed)) return 'code';
  if (/^#+ |^- |\*\*/.test(trimmed)) return 'markdown';
  return 'text';
}

const TYPE_CONFIG: Partial<Record<CanvasContentType, { icon: typeof Code2; label: string; color: string }>> = {
  code:     { icon: Code2,     label: 'Code',     color: '#00D4FF' },
  html:     { icon: Code2,     label: 'HTML',     color: '#FF3366' },
  json:     { icon: Code2,     label: 'JSON',     color: '#FFB300' },
  markdown: { icon: FileText,  label: 'Markdown', color: '#6366F1' },
  text:     { icon: FileText,  label: 'Text',     color: '#A0B4CC' },
  image:    { icon: Image,     label: 'Image',    color: '#00E676' },
};

export function CanvasViewer({ content, contentType: typeProp, language, title, className = '' }: CanvasViewerProps) {
  const type = typeProp ?? detectType(content);
  const cfg = TYPE_CONFIG[type] ?? TYPE_CONFIG.text!;
  const Icon = cfg.icon;

  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div
      ref={containerRef}
      className={`bg-[#0A0F1A] border border-white/[0.08] rounded-xl overflow-hidden ${expanded ? 'fixed inset-4 z-50' : ''} ${className}`}
      role="region"
      aria-label={title ?? `Canvas — ${cfg.label}`}
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-white/[0.06] bg-[#0F1826]">
        <Icon size={13} style={{ color: cfg.color }} aria-hidden />
        <span className="text-[12px] font-semibold" style={{ color: cfg.color }}>
          {title ?? cfg.label}
          {language && ` · ${language}`}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <motion.button
            whileTap={{ scale: 0.9 }}
            transition={SPRING_FAST}
            onClick={handleCopy}
            aria-label="Copy content"
            className="p-1.5 rounded-lg text-[#5A7494] hover:text-[#A0B4CC] hover:bg-white/[0.06]"
          >
            {copied ? <Check size={13} className="text-[#00E676]" aria-hidden /> : <Copy size={13} aria-hidden />}
          </motion.button>
          <motion.button
            whileTap={{ scale: 0.9 }}
            transition={SPRING_FAST}
            onClick={() => setExpanded(x => !x)}
            aria-label={expanded ? 'Minimize' : 'Maximize'}
            className="p-1.5 rounded-lg text-[#5A7494] hover:text-[#A0B4CC] hover:bg-white/[0.06]"
          >
            {expanded ? <Minimize2 size={13} aria-hidden /> : <Maximize2 size={13} aria-hidden />}
          </motion.button>
        </div>
      </div>

      {/* Content */}
      <div className={`overflow-auto ${expanded ? 'h-[calc(100%-48px)]' : 'max-h-96'}`}>
        {type === 'image' ? (
          <img src={content} alt={title ?? 'Canvas image'} className="max-w-full" />
        ) : type === 'html' ? (
          <iframe
            srcDoc={content}
            title={title ?? 'HTML canvas'}
            className="w-full h-full min-h-[300px] border-0 bg-white"
            sandbox="allow-scripts"
          />
        ) : (
          <pre className="p-4 text-[12px] font-mono text-[#A0B4CC] leading-relaxed whitespace-pre-wrap break-words">
            <code>{content}</code>
          </pre>
        )}
      </div>

      {/* Expanded backdrop */}
      {expanded && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="fixed inset-0 bg-black/60 -z-10"
          onClick={() => setExpanded(false)}
          aria-hidden="true"
        />
      )}
    </div>
  );
}
