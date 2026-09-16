import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { ChatImageOutput } from './ChatImageOutput';

describe('ChatImageOutput', () => {
  test('renders the image with the given src and alt', () => {
    render(<ChatImageOutput src="https://example.com/image.png" alt="A cat" />);
    const img = screen.getByAltText('A cat') as HTMLImageElement;
    expect(img).toBeInTheDocument();
    expect(img.src).toBe('https://example.com/image.png');
  });

  test('defaults the alt text to "Output image" when none is given', () => {
    render(<ChatImageOutput src="https://example.com/image.png" />);
    expect(screen.getByAltText('Output image')).toBeInTheDocument();
  });

  test('does not show the lightbox initially', () => {
    render(<ChatImageOutput src="https://example.com/image.png" />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  test('opens the lightbox when the image is clicked', () => {
    render(<ChatImageOutput src="https://example.com/image.png" alt="A cat" />);
    fireEvent.click(screen.getByAltText('A cat'));
    expect(screen.getByRole('dialog', { name: 'Image lightbox' })).toBeInTheDocument();
    // Two copies of the image render while the lightbox is open (thumbnail + lightbox).
    expect(screen.getAllByAltText('A cat')).toHaveLength(2);
  });

  test('opens the lightbox via the zoom button', () => {
    render(<ChatImageOutput src="https://example.com/image.png" />);
    fireEvent.click(screen.getByRole('button', { name: 'Open fullscreen' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  test('closes the lightbox via the close button', () => {
    render(<ChatImageOutput src="https://example.com/image.png" />);
    fireEvent.click(screen.getByRole('button', { name: 'Open fullscreen' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Close lightbox' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  test('closes the lightbox when the backdrop is clicked', () => {
    render(<ChatImageOutput src="https://example.com/image.png" />);
    fireEvent.click(screen.getByRole('button', { name: 'Open fullscreen' }));
    fireEvent.click(screen.getByRole('dialog'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  test('clicking the lightbox image itself does not close it (stopPropagation)', () => {
    render(<ChatImageOutput src="https://example.com/image.png" alt="A cat" />);
    fireEvent.click(screen.getByRole('button', { name: 'Open fullscreen' }));
    const images = screen.getAllByAltText('A cat');
    // The second rendered image is inside the lightbox.
    fireEvent.click(images[1]);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});
