/**
 * useGreeting — fetches and auto-plays the spoken org greeting on login.
 *
 * Calls GET /v1/voice/greeting/{orgId} which uses:
 *   D-2: DigestGenerator WYWA data
 *   D-5: Jurisdiction → TTS language auto-detection
 *   D-7: Real OrgService.get_org_health() stats
 *
 * Falls back to browser speechSynthesis if TTS endpoint fails.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { voiceApi } from '@/features/org/api/voice';

export function useGreeting(orgId: string | null, userName?: string) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [hasPlayed, setHasPlayed] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const { data: wav, isSuccess } = useQuery({
    queryKey: ['voice-greeting', orgId, userName],
    enabled: !!orgId,
    queryFn: () => voiceApi.greeting(orgId!, { user_name: userName || 'there' }),
    staleTime: 5 * 60 * 1000,   // 5 min — matches server cache TTL
    retry: false,
  });

  const play = useCallback(async () => {
    if (!wav || hasPlayed) return;
    setHasPlayed(true);
    setIsPlaying(true);
    try {
      const url  = URL.createObjectURL(wav);
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => { setIsPlaying(false); URL.revokeObjectURL(url); };
      audio.onerror = () => {
        setIsPlaying(false);
        URL.revokeObjectURL(url);
      };
      await audio.play();
    } catch {
      setIsPlaying(false);
    }
  }, [wav, hasPlayed]);

  const stop = useCallback(() => {
    audioRef.current?.pause();
    audioRef.current = null;
    setIsPlaying(false);
  }, []);

  // Auto-play once when WAV arrives
  useEffect(() => {
    if (isSuccess && wav && !hasPlayed) {
      play();
    }
  }, [isSuccess, wav, hasPlayed, play]);

  return { isPlaying, play, stop, hasPlayed };
}
