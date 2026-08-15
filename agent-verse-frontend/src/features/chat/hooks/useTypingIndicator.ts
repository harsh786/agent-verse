/**
 * useTypingIndicator — manages the "•••" typing bubble state.
 *
 * Shows typing indicator immediately when isStreaming is true,
 * hides it when streaming ends.
 */

import { useEffect, useState } from 'react';

export function useTypingIndicator(isStreaming: boolean): boolean {
  const [showTyping, setShowTyping] = useState(false);

  useEffect(() => {
    if (isStreaming) {
      setShowTyping(true);
    } else {
      // Keep it visible briefly for smoothness
      const t = setTimeout(() => setShowTyping(false), 300);
      return () => clearTimeout(t);
    }
  }, [isStreaming]);

  return showTyping;
}
