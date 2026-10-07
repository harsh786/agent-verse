import type { ReactNode } from 'react';
import { TONE_CLASSES, type BadgeTone } from './badgeStyles';

/** One badge for the whole registry page; see badgeStyles for the tones. */
export function Badge({
  tone, children, title, className = '', testId,
}: { tone: BadgeTone; children: ReactNode; title?: string; className?: string; testId?: string }) {
  return (
    <span
      title={title}
      data-testid={testId}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] font-semibold ${TONE_CLASSES[tone]} ${className}`}
    >
      {children}
    </span>
  );
}
