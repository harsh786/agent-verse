export interface Goal {
  goal_id: string;
  goal: string;
  status: 'planning' | 'executing' | 'verifying' | 'complete' | 'failed' | 'cancelled' | 'waiting_human';
  priority: string;
  dry_run: boolean;
  agent_id?: string;
  created_at?: string;
  event_count?: number;
}

export interface GoalEvent {
  type: string;
  step?: string;
  output?: string;
  success?: boolean;
  reason?: string;
  ts?: string;
  [key: string]: unknown;
}

export interface Agent {
  agent_id: string;
  name: string;
  autonomy_mode: string;
  goal_template?: string;
  created_at?: string;
}

export interface Connector {
  server_id: string;
  name: string;
  url: string;
  status?: string;
}

export interface SubmitGoalOptions {
  priority?: string;
  dry_run?: boolean;
  agent_id?: string;
  persistence_mode?: boolean;
  workflow_mode?: string;
}
