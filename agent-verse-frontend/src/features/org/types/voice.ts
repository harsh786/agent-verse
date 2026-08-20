// src/features/org/types/voice.ts
export interface VoiceStatusResponse {
  stt_status:   'ready' | 'idle' | 'error';
  tts_status:   'ready' | 'idle' | 'error';
  stt_model:    string;
  tts_model:    string;
  device:       'cpu' | 'cuda';
  stt_provider: string;
  tts_provider: string;
}

export interface TranscribeResponse {
  transcript:  string;
  language:    string;
  confidence:  number;
  segments:    Array<{ start: number; end: number; text: string }>;
  duration_s:  number;
}

export interface PersonaResponse {
  org_id:        string;
  tenant_id:     string;
  ref_audio_url: string;
  ref_text:      string;
  language:      string;
  created_at:    string;
}

export type VoiceStreamState =
  | 'idle'
  | 'connecting'
  | 'listening'
  | 'processing'
  | 'speaking'
  | 'error';

export interface VoiceStreamEvent {
  type:        'transcript' | 'agent_response' | 'tts_chunk' | 'tts_done'
             | 'agent_thinking' | 'error' | 'session_end';
  text?:       string;
  data?:       string;       // base64 PCM16 at 24 kHz
  is_final?:   boolean;
  confidence?: number;
  language?:   string;
  code?:       string;
  detail?:     string;
}
