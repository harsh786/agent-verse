export interface CoordinationSession {
  session_id: string;
  tenant_id: string;
  state: string;
  next_sequence: number;
  version: number;
}

export interface CoordinationMessage {
  message_id?: string;
  sequence: number;
  sender_agent_id?: string;
  recipient_agent_ids?: string[];
  safe_content?: string;
  artifact_reference?: string;
  classification?: string;
  trust_label?: string;
  citation_ids?: string[];
  compacts_from_sequence?: number;
  compacts_to_sequence?: number;
}

export interface CoordinationEvent {
  event_id: string;
  sequence: number;
  schema_version: number;
  event_type: string;
  session_id: string;
  correlation_id: string;
  causation_id?: string | null;
  producer: string;
  classification: string;
  payload: Record<string, unknown>;
}

export interface CoordinationPage<T = Record<string, unknown>> {
  items: T[];
  next_sequence?: number;
  has_more?: boolean;
}

export interface CoordinationRun {
  session: CoordinationSession;
  messages: CoordinationPage<CoordinationMessage>;
  ledger: Record<string, unknown> | null;
  moa: CoordinationPage;
  camel: CoordinationPage;
  generative: CoordinationPage;
  swarm: { nodes: Array<Record<string, unknown>>; edges: Array<Record<string, unknown>> };
  auction: CoordinationPage & { sealed_bid_count: number };
}
