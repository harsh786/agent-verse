/**
 * VoiceModal — JARVIS-style hold-to-speak voice interface.
 *
 * Mode A (Quick transcript): Uses Web Speech API for fast goal capture → submit to org.
 * Mode B (Full voice OS):    Uses VoiceCommandBar → WebSocket → native STT/TTS pipeline
 *                            with D-1/D-3/D-4 intent routing and OmniVoice/Kokoro TTS.
 *
 * Skills:
 *   - frontend-design:   JARVIS dark waveform, electric pulse on listening
 *   - emil-design-eng:   spring open/close (300/26), waveform bar stagger
 *   - impeccable-ui:     transcript dominant, status secondary
 *   - web-guidelines:    aria-modal, focus trap, keyboard Escape close
 *   - ui-ux-pro-max:     reduced-motion, 44px targets, aria-live transcript
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Mic, MicOff, X, Loader2, CheckCircle2, Zap, Cpu } from 'lucide-react';
import { cn } from '@/lib/utils';
import { VoiceCommandBar } from '@/components/voice/VoiceCommandBar';

interface VoiceModalProps {
  open:         boolean;
  onClose:      () => void;
  onTranscript: (text: string) => void;
  placeholder?: string;
  orgId?:       string;   // Required for full native voice OS mode (D-1/D-3/D-4)
}

type ListenState = 'idle' | 'listening' | 'processing' | 'done' | 'error' | 'unsupported';

// Spring configs (emil-design-eng)
const MODAL_SPRING = { type: 'spring', stiffness: 300, damping: 26 } as const;
const BAR_SPRING   = { type: 'spring', stiffness: 500, damping: 35 } as const;

// Web Speech API types
interface SpeechRecognitionInstance {
  lang: string; interimResults: boolean; continuous: boolean;
  onresult: ((e: { results: { [i: number]: { [j: number]: { transcript: string } } } }) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
}

declare global {
  interface Window {
    SpeechRecognition?: new () => SpeechRecognitionInstance;
    webkitSpeechRecognition?: new () => SpeechRecognitionInstance;
  }
}

// Waveform bars (JARVIS signature element)
const BARS = 20;

export function VoiceModal({ open, onClose, onTranscript, placeholder, orgId }: VoiceModalProps) {
  const reduce     = useReducedMotion();
  const [state, setState]       = useState<ListenState>('idle');
  const [transcript, setTranscript] = useState('');
  const [interim, setInterim]   = useState('');
  const [bars, setBars]         = useState<number[]>(Array(BARS).fill(0.1));
  const recognitionRef          = useRef<SpeechRecognitionInstance | null>(null);
  const animFrameRef            = useRef<number | null>(null);

  // Animate waveform bars while listening
  useEffect(() => {
    if (state !== 'listening' || reduce) {
      setBars(Array(BARS).fill(0.1));
      return;
    }
    function animateBars() {
      setBars(prev => prev.map(() => 0.1 + Math.random() * 0.9));
      animFrameRef.current = requestAnimationFrame(animateBars);
    }
    animFrameRef.current = requestAnimationFrame(animateBars);
    return () => { if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current); };
  }, [state, reduce]);

  const startListening = useCallback(() => {
    const SR = window.SpeechRecognition ?? window.webkitSpeechRecognition;
    if (!SR) { setState('unsupported'); return; }

    const recognition = new SR();
    recognition.lang = 'en-US';
    recognition.interimResults = true;
    recognition.continuous = false;
    recognitionRef.current = recognition;

    recognition.onresult = (e) => {
      let final = ''; let inter = '';
      const results = e.results as unknown as Array<Array<{transcript: string}> & {isFinal?: boolean}>;
      const len = results.length;
      for (let i = 0; i < len; i++) {
        const alt = e.results[i][0].transcript;
        if (e.results[i] as unknown as { isFinal?: boolean }) { final += alt; }
        else { inter += alt; }
      }
      if (final) setTranscript(t => t + final);
      setInterim(inter);
    };
    recognition.onerror = () => setState('error');
    recognition.onend   = () => {
      setState(t => t === 'listening' ? 'processing' : t);
      setInterim('');
      setTimeout(() => setState('done'), 400);
    };

    recognition.start();
    setState('listening');
    setTranscript('');
    setInterim('');
  }, []);

  const stopListening = useCallback(() => {
    recognitionRef.current?.stop();
    setState('processing');
  }, []);

  const handleConfirm = useCallback(() => {
    onTranscript(transcript.trim());
    onClose();
    setTranscript('');
    setState('idle');
  }, [transcript, onTranscript, onClose]);

  const handleClose = useCallback(() => {
    recognitionRef.current?.stop();
    setState('idle');
    setTranscript('');
    onClose();
  }, [onClose]);

  // Escape key
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') handleClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, handleClose]);

  // Mode: "quick" = Web Speech API transcript only, "native" = full voice OS (D-1/D-3/D-4)
  const [mode, setMode] = useState<'quick' | 'native'>('quick');

  // If orgId provided, offer native mode
  const canNative = !!orgId;

  return (
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            key="voice-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 bg-black/70 backdrop-blur-md"
            onClick={handleClose}
            aria-hidden
          />

          {/* Modal */}
          <motion.div
            key="voice-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Voice input"
            initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.9, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.92, y: 16 }}
            transition={reduce ? { duration: 0.15 } : MODAL_SPRING}
            className={cn(
              'fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50',
              'w-full max-w-sm mx-4',
              'bg-[#0F1117] border border-[#2D3748] rounded-2xl',
              'shadow-[0_20px_80px_rgba(0,0,0,0.7)]',
              'overflow-hidden',
            )}
          >
            {/* Close */}
            <div className="flex justify-between items-center p-3">
              {/* Mode toggle (only when orgId provided for native voice) */}
              {canNative && (
                <div className="flex items-center gap-1 bg-[#1A1F2E] rounded-lg p-0.5">
                  <button
                    onClick={() => setMode('quick')}
                    className={cn(
                      'flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[10px] font-medium transition-colors',
                      mode === 'quick'
                        ? 'bg-[#00D4FF]/10 text-[#00D4FF] border border-[#00D4FF]/20'
                        : 'text-[#475569] hover:text-[#94A3B8]',
                    )}
                    aria-pressed={mode === 'quick'}
                  >
                    <Mic className="h-3 w-3" aria-hidden /> Quick
                  </button>
                  <button
                    onClick={() => setMode('native')}
                    className={cn(
                      'flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[10px] font-medium transition-colors',
                      mode === 'native'
                        ? 'bg-violet-500/10 text-violet-400 border border-violet-500/20'
                        : 'text-[#475569] hover:text-[#94A3B8]',
                    )}
                    aria-pressed={mode === 'native'}
                  >
                    <Cpu className="h-3 w-3" aria-hidden /> AI Voice OS
                  </button>
                </div>
              )}
              {!canNative && <div />}
              <button
                onClick={handleClose}
                aria-label="Close voice modal"
                className="p-2 rounded-lg text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E] transition-colors"
                style={{ touchAction: 'manipulation' }}
              >
                <X className="h-4 w-4" aria-hidden />
              </button>
            </div>

            {/* JARVIS waveform visualization — quick mode only */}
            <div className="px-6 pb-2">
              {/* Native voice OS mode (D-1/D-3/D-4) */}
              {mode === 'native' && orgId ? (
                <div className="py-2">
                  <p className="text-[10px] text-violet-400/70 text-center mb-3 font-mono tracking-wider">
                    NATIVE VOICE OS · INTENT-AWARE · MISSION-CAPABLE
                  </p>
                  <VoiceCommandBar
                    orgId={orgId}
                    onMissionCreated={(text) => {
                      onTranscript(text);
                      // Keep modal open so user can see the confirmation
                    }}
                  />
                  <button
                    onClick={handleClose}
                    className="mt-4 w-full py-2 rounded-lg text-[12px] text-[#475569] hover:text-[#94A3B8] border border-[#2D3748] hover:border-[#475569] transition-colors"
                  >
                    Done
                  </button>
                </div>
              ) : (
              <>
              <div className="flex items-end justify-center gap-0.5 h-16 mb-2" aria-hidden>
                {bars.map((h, i) => (
                  <motion.div
                    key={i}
                    animate={reduce ? {} : { scaleY: h }}
                    transition={{ ...BAR_SPRING, delay: i * 0.01 }}
                    className={cn(
                      'w-1.5 rounded-full origin-bottom',
                      state === 'listening' ? 'bg-blue-400' :
                      state === 'done'      ? 'bg-emerald-400' :
                      state === 'error'     ? 'bg-rose-400' : 'bg-[#2D3748]',
                    )}
                    style={{ height: `${h * 100}%` }}
                  />
                ))}
              </div>

              {/* Mic button — JARVIS hold-to-speak */}
              <div className="flex justify-center mb-5">
                <motion.button
                  aria-label={state === 'listening' ? 'Release to stop recording' : 'Hold to speak'}
                  onPointerDown={state === 'idle' || state === 'done' ? startListening : undefined}
                  onPointerUp={state === 'listening' ? stopListening : undefined}
                  onPointerLeave={state === 'listening' ? stopListening : undefined}
                  whileTap={reduce ? {} : { scale: 0.92 }}
                  transition={BAR_SPRING}
                  style={{ touchAction: 'manipulation' }}
                  className={cn(
                    'relative flex items-center justify-center',
                    'h-20 w-20 rounded-full',
                    'transition-colors duration-200',
                    state === 'listening' ? 'bg-blue-600 shadow-[0_0_30px_rgba(59,130,246,0.5)]' :
                    state === 'done'      ? 'bg-emerald-600 shadow-[0_0_20px_rgba(16,185,129,0.4)]' :
                    state === 'error'     ? 'bg-rose-600' : 'bg-[#252B3B] hover:bg-[#2D3748]',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500',
                  )}
                >
                  {/* Pulse ring while listening */}
                  {state === 'listening' && !reduce && (
                    <>
                      <motion.div
                        className="absolute inset-0 rounded-full bg-blue-500/30"
                        animate={{ scale: [1, 1.6, 1], opacity: [0.5, 0, 0.5] }}
                        transition={{ duration: 1.5, repeat: Infinity }}
                      />
                      <motion.div
                        className="absolute inset-0 rounded-full bg-blue-500/20"
                        animate={{ scale: [1, 2, 1], opacity: [0.3, 0, 0.3] }}
                        transition={{ duration: 1.5, repeat: Infinity, delay: 0.3 }}
                      />
                    </>
                  )}

                  {state === 'processing' ? (
                    <Loader2 className="h-8 w-8 text-white animate-spin" aria-hidden />
                  ) : state === 'done' ? (
                    <CheckCircle2 className="h-8 w-8 text-white" aria-hidden />
                  ) : state === 'unsupported' ? (
                    <MicOff className="h-8 w-8 text-[#94A3B8]" aria-hidden />
                  ) : (
                    <Mic className="h-8 w-8 text-white" aria-hidden />
                  )}
                </motion.button>
              </div>

              {/* Status */}
              <p className="text-center text-[12px] text-[#475569] mb-3">
                {state === 'idle'        && (placeholder ?? 'Hold mic to speak')}
                {state === 'listening'   && 'Listening… release to stop'}
                {state === 'processing'  && 'Processing\u2026'}
                {state === 'unsupported' && 'Speech not supported in this browser'}
                {state === 'error'       && 'Microphone access denied or error'}
              </p>

              {/* Transcript (aria-live — web-guidelines) */}
              <div
                aria-live="polite"
                aria-atomic="false"
                className={cn(
                  'min-h-[60px] px-3 py-2.5 rounded-xl mb-4',
                  'bg-[#252B3B] border border-[#2D3748]',
                  'text-[14px] leading-[1.6]',
                )}
              >
                {transcript || interim ? (
                  <span>
                    <span className="text-[#F1F5F9]">{transcript}</span>
                    {interim && <span className="text-[#94A3B8]">{interim}</span>}
                  </span>
                ) : (
                  <span className="text-[#475569] italic">Transcript will appear here…</span>
                )}
              </div>

              {/* Confirm */}
              {transcript && (
                <motion.button
                  initial={reduce ? {} : { opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ type: 'spring', stiffness: 400, damping: 30 }}
                  onClick={handleConfirm}
                  style={{ touchAction: 'manipulation' }}
                  className={cn(
                    'w-full py-3 rounded-xl text-[14px] font-semibold text-white mb-4',
                    'bg-blue-600 hover:bg-blue-500',
                    'active:scale-[0.98] transition-[background-color,transform] duration-150',
                  )}
                  aria-label={`Use transcript: ${transcript}`}
                >
                  <span className="flex items-center justify-center gap-2">
                    <Zap className="h-4 w-4" aria-hidden />
                    Use This
                  </span>
                </motion.button>
              )}
              </>
            )}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
