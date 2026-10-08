// LOCAL ONLY - not committed. Works around a corrupted ffmpeg in this machine's
// Playwright cache (video recording spawns ffmpeg, which macOS SIGKILLs).
import base from './playwright.config';
export default { ...base, use: { ...(base as any).use, video: 'off' as const, trace: 'off' as const } };
