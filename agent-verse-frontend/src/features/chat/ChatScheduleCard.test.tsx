/** Phase 7 — schedule-confirmation card from a schedule_created event. */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ChatScheduleCard } from './ChatScheduleCard';

describe('ChatScheduleCard', () => {
  it('renders the human schedule, cron expression and next run', () => {
    render(
      <ChatScheduleCard
        humanSchedule="Every day at 9:00 AM"
        cronExpression="0 9 * * *"
        nextRunIso="2026-09-16T09:00:00Z"
        goalText="Send the daily report"
      />,
    );
    expect(screen.getByText('Schedule created')).toBeDefined();
    expect(screen.getByText('Send the daily report')).toBeDefined();
    expect(screen.getByText('Every day at 9:00 AM')).toBeDefined();
    expect(screen.getByText('0 9 * * *')).toBeDefined();
    // Next-run value is rendered (locale-formatted), under the "Next run" label.
    expect(screen.getByText('Next run')).toBeDefined();
  });

  it('omits optional rows that are not provided', () => {
    render(<ChatScheduleCard humanSchedule="Hourly" />);
    expect(screen.getByText('Hourly')).toBeDefined();
    expect(screen.queryByText('Cron')).toBeNull();
    expect(screen.queryByText('Next run')).toBeNull();
  });
});
