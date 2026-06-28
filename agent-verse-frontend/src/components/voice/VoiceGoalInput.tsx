/**
 * VoiceGoalInput — dictate a goal using the Web Speech API.
 *
 * Falls back gracefully when speech recognition is not supported.
 * The recognized text is passed to the parent via onTranscript.
 *
 * Usage:
 *   <VoiceGoalInput onTranscript={(text) => setGoalText(text)} />
 */
import { useState, useRef, useCallback } from "react";
import { Mic, MicOff, Loader2 } from "lucide-react";

interface VoiceGoalInputProps {
  onTranscript: (text: string) => void;
  className?: string;
  disabled?: boolean;
}

type VoiceState = "idle" | "listening" | "processing" | "unsupported";

// Minimal types for Web Speech API (not in TypeScript's DOM lib as globals)
interface SpeechRecognitionEvent extends Event {
  readonly results: SpeechRecognitionResultList;
}

interface SpeechRecognitionInstance {
  lang: string;
  interimResults: boolean;
  maxAlternatives: number;
  continuous: boolean;
  onstart: (() => void) | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
  onresult: ((event: SpeechRecognitionEvent) => void) | null;
  start(): void;
  stop(): void;
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionInstance;

// Access webkit-prefixed or standard Speech Recognition API
const getSR = (): SpeechRecognitionConstructor | undefined =>
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition;

export function VoiceGoalInput({ onTranscript, className = "", disabled = false }: VoiceGoalInputProps) {
  const [state, setState] = useState<VoiceState>(() => (getSR() ? "idle" : "unsupported"));
  const recognitionRef = useRef<SpeechRecognitionInstance | null>(null);

  const startListening = useCallback(() => {
    const SR = getSR();
    if (!SR) return;

    const recognition = new SR();
    recognition.lang = "en-US";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognition.continuous = false;

    recognition.onstart = () => setState("listening");
    recognition.onend = () => setState("idle");
    recognition.onerror = () => setState("idle");

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      setState("processing");
      const transcript = event.results[0]?.[0]?.transcript ?? "";
      if (transcript) onTranscript(transcript);
      setTimeout(() => setState("idle"), 300);
    };

    recognitionRef.current = recognition;
    recognition.start();
  }, [onTranscript]);

  const stopListening = useCallback(() => {
    recognitionRef.current?.stop();
    setState("idle");
  }, []);

  if (state === "unsupported") return null; // graceful degradation

  const ICONS = {
    idle: Mic,
    listening: MicOff,
    processing: Loader2,
    unsupported: MicOff,
  };
  const Icon = ICONS[state];

  const LABELS = {
    idle: "Start voice input",
    listening: "Stop listening",
    processing: "Processing…",
    unsupported: "Voice not supported",
  };

  const COLORS = {
    idle: "text-muted-foreground hover:text-foreground",
    listening: "text-red-500 animate-pulse",
    processing: "text-primary animate-spin",
    unsupported: "text-muted-foreground opacity-40",
  };

  return (
    <button
      type="button"
      onClick={state === "listening" ? stopListening : startListening}
      disabled={disabled || state === "processing"}
      className={`p-2 rounded-lg transition-colors disabled:opacity-50 ${COLORS[state]} ${className}`}
      aria-label={LABELS[state]}
      title={LABELS[state]}
    >
      <Icon className="h-4 w-4" aria-hidden="true" />
    </button>
  );
}
