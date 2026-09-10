/**
 * VoiceModal — JARVIS-style real-time voice session.
 *
 * Full rewrite (spec Task 9.2): uses useVoiceStream hook instead of browser APIs.
 *
 * D-1: Transcripts are routed by intent → can create real OrgMissions
 * D-3: Intent classification visible in agent response
 * D-4: Say "approve" → real approval recorded
 *
 * Skills: emil-design-eng (spring 300/26), impeccable-ui, web-guidelines
 */
import { useState, useCallback, useEffect } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Mic, MicOff, X, Loader2, CheckCircle2, Volume2, Zap } from 'lucide-react';
import { cn }             from '@/lib/utils';
import { useVoiceStream } from '@/lib/voice/useVoiceStream';

interface VoiceModalProps {
  open:         boolean;
  onClose:      () => void;
  onTranscript: (text: string) => void;
  orgId?:       string;
  placeholder?: string;
}

const MODAL_SPRING = { type: 'spring', stiffness: 300, damping: 26 } as const;
const BAR_SPRING   = { type: 'spring', stiffness: 500, damping: 35 } as const;
const BARS         = 20;

const STATE_COLOR: Record<string, string> = {
  idle:        'bg-[#1E2535]',
  connecting:  'bg-[#1E2535]',
  listening:   'bg-[#00D4FF]',
  processing:  'bg-amber-400',
  speaking:    'bg-emerald-400',
  error:       'bg-rose-500',
};

const STATE_LABEL: Record<string, string> = {
  idle:        'Click mic to start',
  connecting:  'Connecting…',
  listening:   'Listening…',
  processing:  'Processing…',
  speaking:    'JARVIS speaking…',
  error:       'Error — tap to retry',
};

export function VoiceModal({
  open, onClose, onTranscript, orgId, placeholder,
}: VoiceModalProps) {
  const reduce = useReducedMotion();
  const [transcript,    setTranscript]   = useState('');
  const [interimText,   setInterimText]  = useState('');
  const [agentResponse, setAgentResponse] = useState('');
  const [bars,          setBars]          = useState<number[]>(Array(BARS).fill(0.1));
  const [micActive,     setMicActive]    = useState(false);
  const [errorMsg,      setErrorMsg]     = useState('');

  // When no orgId, voice stream will not connect (noop mode)
  const hasNative = !!orgId;

  const { state, connect, startMic, stopMic, disconnect } = useVoiceStream(
    orgId ?? '__noop__',
    {
      onTranscript: (text, isFinal) => {
        if (isFinal) { setTranscript(text); setInterimText(''); }
        else          { setInterimText(text); }
      },
      onAgentResponse: (text) => setAgentResponse(text),
      onTtsDone:       ()     => { setMicActive(false); },
      onError:         (msg)  => {
        setMicActive(false);
        // Surface the real cause. A denied mic permission is by far the most
        // common reason speech-to-text "doesn't work" — say so plainly.
        const m = String(msg ?? '');
        setErrorMsg(
          /NotAllowed|Permission|denied/i.test(m)
            ? 'Microphone access blocked. Click the mic/lock icon in the address bar and allow the microphone, then retry.'
            : /NotFound|Devices/i.test(m)
              ? 'No microphone found. Connect a mic and retry.'
              : m || 'Voice error — tap the mic to retry.',
        );
      },
    },
  );

  // Animate waveform bars
  useEffect(() => {
    const active = state === 'listening' || state === 'speaking';
    if (!active || reduce) { setBars(Array(BARS).fill(0.1)); return; }
    const tid = setInterval(
      () => setBars(p => p.map(() => 0.1 + Math.random() * 0.9)),
      80,
    );
    return () => clearInterval(tid);
  }, [state, reduce]);

  // Connect / disconnect with modal open state
  useEffect(() => {
    if (open && hasNative) connect();
    if (!open) {
      if (hasNative) disconnect();
      setTranscript(''); setInterimText(''); setAgentResponse('');
      setMicActive(false);
    }
  }, [open, hasNative]); // eslint-disable-line react-hooks/exhaustive-deps

  // Escape key
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') handleClose(); };
    document.addEventListener('keydown', h);
    return () => document.removeEventListener('keydown', h);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleMicToggle = useCallback(async () => {
    if (!hasNative) return;
    if (micActive) { stopMic(); setMicActive(false); }
    else           { setErrorMsg(''); await startMic(); setMicActive(true); }
  }, [hasNative, micActive, startMic, stopMic]);

  const handleConfirm = useCallback(() => {
    const text = (transcript || interimText).trim();
    if (text) onTranscript(text);
    handleClose();
  }, [transcript, interimText, onTranscript]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleClose = useCallback(() => {
    if (micActive) { stopMic(); setMicActive(false); }
    if (hasNative) disconnect();
    setTranscript(''); setInterimText(''); setAgentResponse('');
    onClose();
  }, [micActive, hasNative, stopMic, disconnect, onClose]);

  const barColor = STATE_COLOR[state] ?? 'bg-[#1E2535]';
  const label    = STATE_LABEL[state]  ?? state;

  return (
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            key="voice-backdrop"
            className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={handleClose}
            aria-hidden
          />

          {/* Panel */}
          <motion.div
            key="voice-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Voice command interface"
            className={cn(
              'fixed z-50 inset-x-4 bottom-6',
              'sm:inset-x-auto sm:left-1/2 sm:-translate-x-1/2',
              'w-full sm:w-[480px] max-w-full',
              'bg-[#0D1117] border border-[#1E2535] rounded-2xl p-6 shadow-2xl',
              'flex flex-col gap-5',
            )}
            initial={{ opacity: 0, y: 40, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 40, scale: 0.96 }}
            transition={MODAL_SPRING}
          >
            {/* Header */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                {state === 'speaking'
                  ? <Volume2 className="h-4 w-4 text-[#00D4FF]" aria-hidden />
                  : <Mic     className="h-4 w-4 text-[#00D4FF]" aria-hidden />
                }
                <span className="text-sm font-semibold text-[#F1F5F9]">Voice Command</span>
                {hasNative && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#00D4FF]/10 text-[#00D4FF] font-mono">
                    AI NATIVE
                  </span>
                )}
              </div>
              <button
                onClick={handleClose}
                className="p-1.5 rounded-lg text-[#64748B] hover:text-[#F1F5F9] hover:bg-[#1E2535] transition-colors"
                aria-label="Close voice modal"
                style={{ touchAction: 'manipulation' }}
              >
                <X className="h-4 w-4" aria-hidden />
              </button>
            </div>

            {/* Waveform — JARVIS aesthetic */}
            <div className="flex items-end justify-center gap-1 h-16" aria-hidden="true">
              {bars.map((h, i) => (
                <motion.div
                  key={i}
                  className={cn('w-1 rounded-full', barColor)}
                  animate={{ height: reduce ? '4px' : `${4 + h * 52}px` }}
                  transition={{ ...BAR_SPRING, delay: i * 0.015 }}
                />
              ))}
            </div>

            {/* State label */}
            <p className="text-center text-xs text-[#64748B] font-medium tracking-widest uppercase">
              {label}
            </p>

            {/* Error detail — tells the user the real reason (usually mic permission) */}
            {errorMsg && (
              <p
                className="text-center text-[12px] text-rose-300 bg-rose-500/10 border border-rose-500/20 rounded-lg px-3 py-2 leading-snug"
                role="alert"
              >
                {errorMsg}
              </p>
            )}

            {/* Mic button */}
            <div className="flex justify-center">
              <motion.button
                onClick={hasNative ? handleMicToggle : undefined}
                whileTap={reduce ? {} : { scale: 0.92 }}
                transition={BAR_SPRING}
                style={{ touchAction: 'manipulation' }}
                aria-label={micActive ? 'Stop recording' : 'Start voice input'}
                className={cn(
                  'relative flex items-center justify-center h-20 w-20 rounded-full',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/60',
                  'transition-all duration-200',
                  state === 'listening' ? 'bg-[#00D4FF]/20 border-2 border-[#00D4FF]/60' :
                  state === 'speaking'  ? 'bg-emerald-500/20 border-2 border-emerald-500/60' :
                  state === 'error'     ? 'bg-rose-500/20 border-2 border-rose-500/60' :
                  'bg-[#1E2535] border-2 border-[#2D3748] hover:border-[#00D4FF]/30',
                )}
              >
                {/* Pulse ring */}
                {(state === 'listening' || state === 'speaking') && !reduce && (
                  <motion.div
                    className="absolute inset-0 rounded-full border border-[#00D4FF]/30"
                    animate={{ scale: [1, 1.5], opacity: [0.5, 0] }}
                    transition={{ duration: 1.2, repeat: Infinity }}
                  />
                )}

                {state === 'processing' ? (
                  <Loader2 className="h-8 w-8 text-amber-400 animate-spin" aria-hidden />
                ) : state === 'speaking' ? (
                  <Volume2 className="h-8 w-8 text-emerald-400" aria-hidden />
                ) : micActive ? (
                  <MicOff className="h-8 w-8 text-rose-400" aria-hidden />
                ) : (
                  <Mic className="h-8 w-8 text-[#00D4FF]" aria-hidden />
                )}
              </motion.button>
            </div>

            {/* Transcript display */}
            <div
              className="min-h-[64px] rounded-xl bg-[#1A1F2E] border border-[#1E2535] px-4 py-3"
              aria-live="polite"
              aria-label="Voice transcript"
            >
              {transcript || interimText ? (
                <p className="text-sm text-[#F1F5F9] leading-relaxed">
                  {transcript}
                  {interimText && (
                    <span className="text-[#64748B] italic"> {interimText}</span>
                  )}
                </p>
              ) : (
                <p className="text-sm text-[#475569] italic">
                  {placeholder ?? 'Speak a mission goal, command, or "approve"…'}
                </p>
              )}
            </div>

            {/* Agent response */}
            {agentResponse && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                className="rounded-xl bg-[#0D1526]/80 border border-[#1E2D4A] px-4 py-3 overflow-hidden"
                aria-live="assertive"
              >
                <div className="flex items-start gap-2">
                  <Zap className="h-3.5 w-3.5 text-[#00D4FF]/60 mt-0.5 shrink-0" aria-hidden />
                  <p className="text-[11px] text-[#94A3B8] leading-relaxed">{agentResponse}</p>
                </div>
              </motion.div>
            )}

            {/* Confirm / submit button */}
            {(transcript || interimText) && (
              <motion.button
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                transition={MODAL_SPRING}
                onClick={handleConfirm}
                style={{ touchAction: 'manipulation' }}
                className={cn(
                  'w-full py-3 rounded-xl text-sm font-semibold text-white',
                  'bg-blue-600 hover:bg-blue-500',
                  'active:scale-[0.98] transition-all duration-150',
                  'flex items-center justify-center gap-2',
                )}
                aria-label={`Use transcript: ${transcript || interimText}`}
              >
                <CheckCircle2 className="h-4 w-4" aria-hidden />
                Use This
              </motion.button>
            )}
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
