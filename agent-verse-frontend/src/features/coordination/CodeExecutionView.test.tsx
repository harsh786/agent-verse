import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { CodeExecutionView } from './CodeExecutionView';

describe('CodeExecutionView', () => {
  test('renders nothing under the heading when executions is empty', () => {
    render(<CodeExecutionView executions={[]} />);
    expect(screen.getByText('Governed code')).toBeInTheDocument();
    expect(screen.queryByRole('listitem')).not.toBeInTheDocument();
  });

  test('renders language, state, exit code and stdout_summary', () => {
    render(
      <CodeExecutionView
        executions={[{ execution_id: 'e1', language: 'python', state: 'completed', exit_code: 0, stdout_summary: '2 rows returned' }]}
      />,
    );
    expect(screen.getByText('python')).toBeInTheDocument();
    expect(screen.getByText('completed · exit 0')).toBeInTheDocument();
    expect(screen.getByText('2 rows returned')).toBeInTheDocument();
  });

  test('falls back to stderr_summary when stdout_summary is absent', () => {
    render(
      <CodeExecutionView executions={[{ execution_id: 'e2', stderr_summary: 'SyntaxError: unexpected token' }]} />,
    );
    expect(screen.getByText('SyntaxError: unexpected token')).toBeInTheDocument();
  });

  test('falls back to "No output" when neither stdout nor stderr summaries exist', () => {
    render(<CodeExecutionView executions={[{ execution_id: 'e3' }]} />);
    expect(screen.getByText('No output')).toBeInTheDocument();
  });

  test('falls back to "unknown" language/state and em-dash exit code when missing', () => {
    render(<CodeExecutionView executions={[{}]} />);
    expect(screen.getByText('unknown')).toBeInTheDocument();
    expect(screen.getByText('unknown · exit —')).toBeInTheDocument();
  });

  test('renders multiple executions keyed by index when execution_id is missing', () => {
    render(
      <CodeExecutionView executions={[{ language: 'js' }, { language: 'go' }]} />,
    );
    expect(screen.getByText('js')).toBeInTheDocument();
    expect(screen.getByText('go')).toBeInTheDocument();
  });
});
