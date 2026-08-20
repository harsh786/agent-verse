/**
 * VoiceCommandBar — real-time voice interface for the org command center.
 *
 * Replaces the stub window.speechSynthesis in VoiceModal with the full
 * native voice OS: faster-whisper STT → IntentRouter → OmniVoice/Kokoro TTS.
 *
 * Features:
 *   D-1: Voice-to-Mission — speak a goal → real OrgMission created
 *   D-3: Intent classification shown to user (create_mission / approve / etc.)
 *   D-4: Approval voice command shown visually
 *   Real-time transcript display with confidence indicator
 *   TTS playback of agent responses (PCM16 via Web Audio API)
 *
 * Uses useVoiceStream hook for all WebSocket management.
 */
import { useCallback, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Mic, MicOff, Loader2, Volume2, Zap, CheckCircle2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useVoiceStream } from '@/lib/voice/useVoiceStream';
import type { VoiceStreamState } from '@/features/org/types/voice';

interface VoiceCommandBarProps {
  orgId: string;
  onMissionCreated?: (text: string) => void;
  className?: string;
}

const STATE_LABELS: Record<VoiceStreamState, string> = {
  idle:        'Click mic to start',
  connecting:  'Connecting...',
  listening:   'Listening...',
  processing:  'Processing...',
  speaking:    'Speaking...',
  error:       'Error — tap to retry',
};

const STATE_COLORS: Record<VoiceStreamState, string> = {
  idle:        'text-[#475569]',
  connecting:  'text-[#94A3B8]',
  listening:   'text-[#00D4FF]',
  processing:  'text-amber-400',
  speaking:    'text-emerald-400',
  error:       'text-rose-400',
};

export function VoiceCommandBar({ orgId, onMissionCreated, className }: VoiceCommandBarProps) {
  const [transcript, setTranscript] = useState('');
  const [response, setResponse]     = useState('');
  const [isFinal, setIsFinal]       = useState(false);
  const [lastIntent, setLastIntent] = useState('');

  const { state, startMic, stopMic, disconnect } = useVoiceStream(orgId, {
    onTranscript: (text, final, confidence) => {
      setTranscript(text);
      setIsFinal(final);
      if (final && confidence < 0.4) {
        setTranscript(prev => prev + ' (low confidence)');
      }
    },
    onAgentThinking: () => setResponse(''),
    onAgentResponse: (text) => {
      setResponse(text);
      // Detect intent from response text for visual feedback
      if (text.includes('Mission created')) {
        setLastIntent('create_mission');
        onMissionCreated?.(text);
      } else if (text.includes('Approved')) {
        setLastIntent('approve');
      }
    },
    onError: (msg) => setResponse(`Error: ${msg}`),
    onSessionEnd: () => { setTranscript(''); setResponse(''); },
  });

  const handleMicToggle = useCallback(async () => {
    if (state === 'idle' || state === 'error') {
      setTranscript('');
      setResponse('');
      await startMic();
    } else if (state === 'listening') {
      stopMic();
    } else {
      disconnect();
    }
  }, [state, startMic, stopMic, disconnect]);

  const isActive = state === 'listening' || state === 'processing' || state === 'speaking';

  return (
    <div className={cn('flex flex-col gap-3', className)}>
      {/* Control row */}
      <div className="flex items-center gap-3">
        {/* Mic button */}
        <button
          onClick={handleMicToggle}
          aria-label={isActive ? 'Stop voice input' : 'Start voice input'}
          style={{ touchAction: 'manipulation' }}
          className={cn(
            'relative w-12 h-12 rounded-full flex items-center justify-center',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/60',
            'transition-all duration-200',
            state === 'error'
              ? 'bg-rose-500/20 border border-rose-500/40 text-rose-400'
              : isActive
              ? 'bg-[#00D4FF]/10 border border-[#00D4FF]/40 text-[#00D4FF]'
              : 'bg-[#1A1F2E] border border-[#2D3748] text-[#475569] hover:text-[#94A3B8] hover:border-[#475569]',
          )}
        >
          {/* Pulse ring when listening */}
          {state === 'listening' && (
            <motion.div
              className="absolute inset-0 rounded-full border border-[#00D4FF]/40"
              animate={{ scale: [1, 1.5, 1], opacity: [0.6, 0, 0.6] }}
              transition={{ duration: 1.5, repeat: Infinity }}
            />
          )}
          {state === 'processing' ? (
            <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
          ) : state === 'speaking' ? (
            <Volume2 className="h-5 w-5" aria-hidden />
          ) : isActive ? (
            <MicOff className="h-5 w-5" aria-hidden />
          ) : (
            <Mic className="h-5 w-5" aria-hidden />
          )}
        </button>

        {/* State label */}
        <div className="flex-1 min-w-0">
          <p className={cn('text-xs font-medium', STATE_COLORS[state])}>
            {STATE_LABELS[state]}
          </p>

          {/* Transcript */}
          <AnimatePresence mode="wait">
            {transcript && (
              <motion.p
                key={transcript}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.15 }}
                className={cn(
                  'text-[11px] mt-0.5 truncate',
                  isFinal ? 'text-[#94A3B8]' : 'text-[#475569]',
                )}
              >
                {isFinal ? '✓ ' : '○ '}{transcript}
              </motion.p>
            )}
          </AnimatePresence>
        </div>

        {/* Intent badge */}
        <AnimatePresence>
          {lastIntent === 'create_mission' && (
            <motion.span
              initial={{ scale: 0 }}
              animate={{ scale: 1 }}
              exit={{ scale: 0 }}
              className="flex items-center gap-1 px-2 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-[10px] font-medium"
            >
              <Zap className="h-3 w-3" aria-hidden />
              MISSION
            </motion.span>
          )}
          {lastIntent === 'approve' && (
            <motion.span
              initial={{ scale: 0 }}
              animate={{ scale: 1 }}
              exit={{ scale: 0 }}
              className="flex items-center gap-1 px-2 py-1 rounded-full bg-blue-500/10 border border-blue-500/30 text-blue-400 text-[10px] font-medium"
            >
              <CheckCircle2 className="h-3 w-3" aria-hidden />
              APPROVED
            </motion.span>
          )}
        </AnimatePresence>
      </div>

      {/* Agent response */}
      <AnimatePresence>
        {response && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ type: 'spring', stiffness: 300, damping: 28 }}
            className="overflow-hidden"
          >
            <div className="p-3 rounded-lg bg-[#0D1526]/60 border border-[#1E2D4A]">
              <div className="flex items-start gap-2">
                {state === 'speaking' ? (
                  <motion.div
                    animate={{ opacity: [1, 0.4, 1] }}
                    transition={{ duration: 0.8, repeat: Infinity }}
                  >
                    <Volume2 className="h-3.5 w-3.5 text-[#00D4FF] mt-0.5 shrink-0" aria-hidden />
                  </motion.div>
                ) : (
                  <Zap className="h-3.5 w-3.5 text-[#00D4FF]/60 mt-0.5 shrink-0" aria-hidden />
                )}
                <p className="text-[11px] text-[#94A3B8] leading-relaxed">{response}</p>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
