import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { GoalEvidencePanel } from './GoalEvidencePanel';
import type { ResultArtifact } from '../resultArtifact';

function artifactWithEvidence(evidence: ResultArtifact['evidence']): ResultArtifact {
  return {
    version: 1,
    kind: 'table',
    title: 'Jira issues',
    summary: 'Found 1 Jira issue.',
    status: 'success',
    metrics: [],
    tables: [],
    evidence,
    downloads: ['json'],
    debug: {},
  };
}

describe('GoalEvidencePanel', () => {
  test('renders an explicit empty state when evidence is empty', () => {
    render(<GoalEvidencePanel artifact={artifactWithEvidence({})} />);

    expect(screen.getByRole('heading', { name: 'Evidence' })).toBeInTheDocument();
    expect(screen.getByText('No evidence available yet.')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Tool evidence' })).not.toBeInTheDocument();
  });

  test('renders artifact evidence verification text', () => {
    render(
      <GoalEvidencePanel
        artifact={artifactWithEvidence({
          verification: 'Goal was achieved because Jira returned matching issues.',
        })}
      />
    );

    expect(screen.getByRole('heading', { name: 'Evidence' })).toBeInTheDocument();
    expect(
      screen.getByText('Goal was achieved because Jira returned matching issues.')
    ).toBeInTheDocument();
  });

  test('renders tool evidence with names, server ids, and statuses', () => {
    render(
      <GoalEvidencePanel
        artifact={artifactWithEvidence({
          tools: [
            { name: 'jira_search_issues', server_id: 'jira-prod', success: true },
            { name: 'slack_post_message', server_id: 'slack-team', success: false },
          ],
        })}
      />
    );

    expect(screen.getByRole('heading', { name: 'Tool evidence' })).toBeInTheDocument();
    expect(screen.getByText('jira_search_issues')).toBeInTheDocument();
    expect(screen.getByText('jira-prod')).toBeInTheDocument();
    expect(screen.getByText('slack_post_message')).toBeInTheDocument();
    expect(screen.getByText('slack-team')).toBeInTheDocument();
    expect(screen.getByText('Success')).toBeInTheDocument();
    expect(screen.getByText('Failed')).toBeInTheDocument();
  });

  test('wraps long tool names and values safely', () => {
    const longToolName = 'tool_' + 'very_long_unbroken_name_'.repeat(8);
    const longServerId = 'server_' + 'very_long_unbroken_value_'.repeat(8);

    render(
      <GoalEvidencePanel
        artifact={artifactWithEvidence({
          tools: [{ name: longToolName, server_id: longServerId, success: true }],
        })}
      />
    );

    const toolName = screen.getByText(longToolName);
    const serverId = screen.getByText(longServerId);

    expect(toolName).toHaveClass('min-w-0', 'break-words');
    expect(toolName.closest('span')).toHaveClass('min-w-0');
    expect(serverId).toHaveClass('break-words');
    expect(serverId.closest('li')).toHaveClass('min-w-0');
  });

  test('renders query and connector evidence', () => {
    render(
      <GoalEvidencePanel
        artifact={artifactWithEvidence({
          query: 'assignee = currentUser() ORDER BY created DESC',
          connector: 'PineLabs JIRA',
        })}
      />
    );

    expect(screen.getByText('Query')).toBeInTheDocument();
    expect(screen.getByText('assignee = currentUser() ORDER BY created DESC')).toBeInTheDocument();
    expect(screen.getByText('Connector')).toBeInTheDocument();
    expect(screen.getByText('PineLabs JIRA')).toBeInTheDocument();
  });
});
