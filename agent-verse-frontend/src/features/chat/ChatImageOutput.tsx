/**
 * ChatImageOutput — image with fullscreen lightbox on click.
 */

import { useState, type JSX } from 'react';
import { X, ZoomIn } from 'lucide-react';

interface Props {
  src: string;
  alt?: string;
}

export function ChatImageOutput({ src, alt = 'Output image' }: Props): JSX.Element {
  const [lightbox, setLightbox] = useState(false);

  return (
    <>
      <div className="relative rounded-xl overflow-hidden border border-gray-200 dark:border-gray-700 group max-w-sm">
        <img
          src={src}
          alt={alt}
          className="w-full h-auto object-cover cursor-zoom-in"
          onClick={() => setLightbox(true)}
        />
        <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            className="p-1.5 bg-black/50 text-white rounded-full"
            onClick={() => setLightbox(true)}
            aria-label="Open fullscreen"
          >
            <ZoomIn className="w-4 h-4" />
          </button>
        </div>
      </div>

      {lightbox && (
        <div
          className="fixed inset-0 z-50 bg-black/90 flex items-center justify-center"
          onClick={() => setLightbox(false)}
          role="dialog"
          aria-modal="true"
          aria-label="Image lightbox"
        >
          <button
            className="absolute top-4 right-4 text-white p-2 hover:bg-white/10 rounded-full"
            onClick={() => setLightbox(false)}
            aria-label="Close lightbox"
          >
            <X className="w-6 h-6" />
          </button>
          <img
            src={src}
            alt={alt}
            className="max-w-[90vw] max-h-[90vh] object-contain rounded-lg"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </>
  );
}
