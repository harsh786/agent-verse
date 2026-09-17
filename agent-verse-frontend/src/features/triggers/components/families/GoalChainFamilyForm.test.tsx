import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { GoalChainFamilyForm } from './GoalChainFamilyForm';

/** Grab the object the component most recently handed to onChange. */
function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

afterEach(() => vi.restoreAllMocks());

describe('GoalChainFamilyForm', () => {
  test('renders the base watch fields with empty defaults for an unrelated trigger type', () => {
    render(<GoalChainFamilyForm triggerType="goal_completed" value={{}} onChange={vi.fn()} />);
    expect(screen.getByPlaceholderText('goal-uuid')).toHaveValue('');
    expect(screen.getByPlaceholderText('agent-uuid')).toHaveValue('');
    expect(screen.queryByPlaceholderText('accuracy')).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText('queue-uuid')).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText('learning')).not.toBeInTheDocument();
  });

  test('editing watch goal id merges into value', () => {
    const onChange = vi.fn();
    render(<GoalChainFamilyForm triggerType="goal_failed" value={{ description: 'x' }} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('goal-uuid'), { target: { value: 'goal-42' } });
    expect(lastArg(onChange)).toEqual({ description: 'x', watch_goal_id: 'goal-42' });
  });

  test('editing watch agent id merges into value', () => {
    const onChange = vi.fn();
    render(<GoalChainFamilyForm triggerType="goal_failed" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('agent-uuid'), { target: { value: 'agent-7' } });
    expect(lastArg(onChange)).toEqual({ watch_agent_id: 'agent-7' });
  });

  describe('goal_score_below', () => {
    test('renders score threshold defaulted to 0.7 and an empty score dimension', () => {
      render(<GoalChainFamilyForm triggerType="goal_score_below" value={{}} onChange={vi.fn()} />);
      expect(screen.getByDisplayValue('0.7')).toBeInTheDocument();
      expect(screen.getByPlaceholderText('accuracy')).toHaveValue('');
    });

    test('changing score threshold coerces to a number', () => {
      const onChange = vi.fn();
      render(<GoalChainFamilyForm triggerType="goal_score_below" value={{}} onChange={onChange} />);
      fireEvent.change(screen.getByDisplayValue('0.7'), { target: { value: '0.4' } });
      expect(lastArg(onChange)).toEqual({ score_threshold: 0.4 });
    });

    test('editing score dimension merges into value', () => {
      const onChange = vi.fn();
      render(<GoalChainFamilyForm triggerType="goal_score_below" value={{}} onChange={onChange} />);
      fireEvent.change(screen.getByPlaceholderText('accuracy'), { target: { value: 'latency' } });
      expect(lastArg(onChange)).toEqual({ score_dimension: 'latency' });
    });

    test('shows a passed score threshold value', () => {
      render(<GoalChainFamilyForm triggerType="goal_score_below" value={{ score_threshold: 0.2 }} onChange={vi.fn()} />);
      expect(screen.getByDisplayValue('0.2')).toBeInTheDocument();
    });
  });

  describe('hitl_approved / hitl_rejected', () => {
    test('renders HITL queue id field for hitl_approved', () => {
      render(<GoalChainFamilyForm triggerType="hitl_approved" value={{}} onChange={vi.fn()} />);
      expect(screen.getByPlaceholderText('queue-uuid')).toHaveValue('');
    });

    test('renders HITL queue id field for hitl_rejected', () => {
      render(<GoalChainFamilyForm triggerType="hitl_rejected" value={{}} onChange={vi.fn()} />);
      expect(screen.getByPlaceholderText('queue-uuid')).toHaveValue('');
    });

    test('editing HITL queue id merges into value', () => {
      const onChange = vi.fn();
      render(<GoalChainFamilyForm triggerType="hitl_rejected" value={{}} onChange={onChange} />);
      fireEvent.change(screen.getByPlaceholderText('queue-uuid'), { target: { value: 'queue-9' } });
      expect(lastArg(onChange)).toEqual({ hitl_queue_id: 'queue-9' });
    });
  });

  describe('memory_created', () => {
    test('renders memory type field with an empty default', () => {
      render(<GoalChainFamilyForm triggerType="memory_created" value={{}} onChange={vi.fn()} />);
      expect(screen.getByPlaceholderText('learning')).toHaveValue('');
    });

    test('editing memory type merges into value', () => {
      const onChange = vi.fn();
      render(<GoalChainFamilyForm triggerType="memory_created" value={{}} onChange={onChange} />);
      fireEvent.change(screen.getByPlaceholderText('learning'), { target: { value: 'fact' } });
      expect(lastArg(onChange)).toEqual({ memory_type: 'fact' });
    });

    test('shows a passed memory type value', () => {
      render(<GoalChainFamilyForm triggerType="memory_created" value={{ memory_type: 'fact' }} onChange={vi.fn()} />);
      expect(screen.getByPlaceholderText('learning')).toHaveValue('fact');
    });
  });
});
