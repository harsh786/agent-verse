/**
 * useLoginGreeting — fetch and auto-play OmniVoice org greeting on first login.
 *
 * - Plays ONCE per browser session (sessionStorage flag per orgId)
 * - Respects prefers-reduced-motion: skips audio if motion is reduced
 * - Uses voiceApi.greeting() → GET /v1/voice/greeting/{org_id}
 *   which uses real OrgService.get_org_health() + DigestGenerator (D-2/D-7)
 * - Follows TanStack Query staleTime pattern from useOrg.ts
 *
 * This is the spec-compliant replacement for the stub useGreeting.ts
 */
import { useEffect, useRef, useState } from 'react';
import { useQuery }                    from '@tanstack/react-query';
import { voiceApi }                    from '@/features/org/api/voice';

export interface UseLoginGreetingOpts {
  orgId:     string;
  userName:  string;
  language?: string;
  enabled?:  boolean;
}

const SESSION_KEY = (orgId: string) => `av:greeting-played:${orgId}`;

export function useLoginGreeting({
  orgId,
  userName,
  language = 'en',
  enabled  = true,
}: UseLoginGreetingOpts) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [hasPlayed, setHasPlayed] = useState(() =>
    typeof window !== 'undefined'
      ? sessionStorage.getItem(SESSION_KEY(orgId)) === '1'
      : false,
  );
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const reducedMotion =
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const { data: blob } = useQuery<Blob>({
    queryKey:  ['voice-greeting', orgId, userName, language],
    queryFn:   () => voiceApi.greeting(orgId, { user_name: userName, language }),
    enabled:   enabled && !hasPlayed && !reducedMotion && !!orgId,
    staleTime: 5 * 60_000,
    retry:     1,
    gcTime:    10 * 60_000,
  });

  useEffect(() => {
    if (!blob || hasPlayed) return;
    const url   = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audioRef.current = audio;

    audio.onplay   = () => setIsPlaying(true);
    audio.onpause  = () => setIsPlaying(false);
    audio.onended  = () => {
      setIsPlaying(false);
      setHasPlayed(true);
      sessionStorage.setItem(SESSION_KEY(orgId), '1');
      URL.revokeObjectURL(url);
    };
    audio.onerror = () => { setIsPlaying(false); URL.revokeObjectURL(url); };

    // 800 ms delay: page renders before audio starts (spec requirement)
    const tid = setTimeout(() => audio.play().catch(() => {}), 800);
    return () => { clearTimeout(tid); };
  }, [blob, hasPlayed, orgId]);

  const stop = () => {
    audioRef.current?.pause();
    setIsPlaying(false);
  };

  const resetAndReplay = () => {
    sessionStorage.removeItem(SESSION_KEY(orgId));
    setHasPlayed(false);
  };

  return { isPlaying, hasPlayed, stop, resetAndReplay };
}
