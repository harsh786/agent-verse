/**
 * ChatModelSelector — dropdown to select LLM model + regenerate-with-model menu.
 */

import { useState, type JSX } from 'react';
import { ChevronDown, Cpu } from 'lucide-react';

interface Props {
  models: string[];
  selected: string;
  onChange: (model: string) => void;
}

export function ChatModelSelector({ models, selected, onChange }: Props): JSX.Element {
  const [open, setOpen] = useState(false);

  return (
    <div className="relative">
      <button
        className="flex items-center gap-1.5 text-xs text-muted-foreground/70 hover:text-muted-foreground transition-colors px-2 py-1 rounded-lg hover:bg-muted"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label="Select model"
      >
        <Cpu className="w-3 h-3" />
        <span className="max-w-28 truncate">{selected || 'Select model'}</span>
        <ChevronDown className="w-3 h-3" />
      </button>

      {open && (
        <div
          className="absolute bottom-full left-0 mb-1 z-50 w-52 bg-card border border-border rounded-xl shadow-xl overflow-hidden"
          role="listbox"
        >
          {models.map((m) => (
            <button
              key={m}
              role="option"
              aria-selected={m === selected}
              className={[
                'flex items-center w-full px-3 py-2 text-xs text-left transition-colors',
                m === selected
                  ? 'bg-indigo-50 dark:bg-indigo-950 text-indigo-700 dark:text-indigo-300 font-medium'
                  : 'text-muted-foreground hover:bg-muted',
              ].join(' ')}
              onClick={() => { onChange(m); setOpen(false); }}
            >
              {m}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
