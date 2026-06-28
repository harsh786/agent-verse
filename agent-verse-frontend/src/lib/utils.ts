import { clsx, type ClassValue } from 'clsx';

/**
 * Utility for constructing class name strings conditionally.
 * Uses clsx for conditional joining; upgrade to twMerge when Tailwind
 * class deduplication is needed (install tailwind-merge).
 */
export function cn(...inputs: ClassValue[]): string {
  return clsx(inputs);
}
