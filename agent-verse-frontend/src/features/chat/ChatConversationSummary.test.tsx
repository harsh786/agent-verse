import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { ChatConversationSummary } from './ChatConversationSummary';

afterEach(() => vi.restoreAllMocks());

describe('ChatConversationSummary', () => {
  test('renders a plain string summary as body text', () => {
    render(<ChatConversationSummary summary="We shipped the release." />);
    expect(screen.getByText('Conversation Summary')).toBeInTheDocument();
    expect(screen.getByText('We shipped the release.')).toBeInTheDocument();
  });

  test('renders the topic from a structured summary', () => {
    render(<ChatConversationSummary summary={{ topic: 'Release planning' }} />);
    expect(screen.getByText('Release planning')).toBeInTheDocument();
  });

  test('renders the "What we did" list items', () => {
    render(
      <ChatConversationSummary
        summary={{ topic: 'T', what_we_did: ['Merged PR #1', 'Deployed to staging'] }}
      />,
    );
    expect(screen.getByText('What we did:')).toBeInTheDocument();
    expect(screen.getByText('Merged PR #1')).toBeInTheDocument();
    expect(screen.getByText('Deployed to staging')).toBeInTheDocument();
  });

  test('renders the Outstanding list items', () => {
    render(
      <ChatConversationSummary summary={{ outstanding: ['Write tests', 'Update docs'] }} />,
    );
    expect(screen.getByText('Outstanding:')).toBeInTheDocument();
    expect(screen.getByText('Write tests')).toBeInTheDocument();
    expect(screen.getByText('Update docs')).toBeInTheDocument();
  });

  test('omits empty structured sections', () => {
    render(<ChatConversationSummary summary={{ topic: 'T', what_we_did: [], outstanding: [] }} />);
    expect(screen.getByText('T')).toBeInTheDocument();
    expect(screen.queryByText('What we did:')).not.toBeInTheDocument();
    expect(screen.queryByText('Outstanding:')).not.toBeInTheDocument();
  });

  test('renders both sections together for a full structured summary', () => {
    render(
      <ChatConversationSummary
        summary={{
          topic: 'Sprint recap',
          what_we_did: ['Closed 5 issues'],
          outstanding: ['Review the RFC'],
          message_count: 12,
        }}
      />,
    );
    expect(screen.getByText('Sprint recap')).toBeInTheDocument();
    expect(screen.getByText('Closed 5 issues')).toBeInTheDocument();
    expect(screen.getByText('Review the RFC')).toBeInTheDocument();
  });
});
