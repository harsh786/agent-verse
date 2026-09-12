/**
 * AdvancedSection — a collapsible group for less-common, cross-cutting step
 * settings (timeout / retry / on-failure). Collapsed by default to keep the
 * common config uncluttered.
 */
import { useState, type ReactNode } from 'react';
import { ChevronRight, SlidersHorizontal } from 'lucide-react';

export function CollapsibleSection({
  title, children, defaultOpen = false, icon,
}: {
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
  icon?: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-t border-white/8 pt-3">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 w-full text-xs font-semibold uppercase
                   tracking-wider text-white/35 hover:text-white/60 transition-colors"
        aria-expanded={open}
      >
        <ChevronRight
          className={`h-3.5 w-3.5 transition-transform ${open ? 'rotate-90' : ''}`}
        />
        {icon ?? <SlidersHorizontal className="h-3.5 w-3.5" />}
        {title}
      </button>
      {open && <div className="mt-3 space-y-4">{children}</div>}
    </div>
  );
}
