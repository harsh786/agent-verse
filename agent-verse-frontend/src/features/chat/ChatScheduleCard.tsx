/**
 * ChatScheduleCard — confirmation card for a created schedule/trigger.
 *
 * Renders from a `schedule_created` SSE event (or a dispatch
 * `schedule_confirmation`): the human-readable cadence, the cron expression,
 * and the next run time.
 */

import { type JSX } from 'react';
import { CalendarClock } from 'lucide-react';

interface Props {
  humanSchedule?: string;
  cronExpression?: string;
  nextRunIso?: string | null;
  goalText?: string;
}

function formatNextRun(iso?: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function ChatScheduleCard({
  humanSchedule,
  cronExpression,
  nextRunIso,
  goalText,
}: Props): JSX.Element {
  const nextRun = formatNextRun(nextRunIso);

  return (
    <div
      className="border border-purple-200 dark:border-purple-800 bg-purple-50 dark:bg-purple-950 rounded-xl p-4 my-2"
      data-testid="chat-schedule-card"
    >
      <div className="flex items-center gap-2 mb-2">
        <CalendarClock className="w-4 h-4 text-purple-600" />
        <span className="text-sm font-medium text-purple-700 dark:text-purple-300">
          Schedule created
        </span>
      </div>

      {goalText && (
        <p className="text-sm font-medium text-[#F0F6FF] dark:text-gray-200 mb-2">{goalText}</p>
      )}

      <dl className="space-y-1 text-xs">
        {humanSchedule && (
          <div className="flex gap-2">
            <dt className="text-[#5A7494] w-20 shrink-0">Runs</dt>
            <dd className="text-[#A0B4CC] dark:text-gray-300">{humanSchedule}</dd>
          </div>
        )}
        {cronExpression && (
          <div className="flex gap-2">
            <dt className="text-[#5A7494] w-20 shrink-0">Cron</dt>
            <dd className="font-mono text-[#A0B4CC] dark:text-gray-300">{cronExpression}</dd>
          </div>
        )}
        {nextRun && (
          <div className="flex gap-2">
            <dt className="text-[#5A7494] w-20 shrink-0">Next run</dt>
            <dd className="text-[#A0B4CC] dark:text-gray-300">{nextRun}</dd>
          </div>
        )}
      </dl>
    </div>
  );
}
