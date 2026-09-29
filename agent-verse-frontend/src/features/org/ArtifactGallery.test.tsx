import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { ArtifactGallery } from './ArtifactGallery';

afterEach(() => vi.restoreAllMocks());

describe('ArtifactGallery', () => {
  test('says artifacts are not available instead of claiming "No artifacts yet"', () => {
    // Regression: it fetched a non-existent /v1/org/{id}/artifacts and
    // swallowed the 404 into an empty list.
    render(<ArtifactGallery orgId="org-1" missionId="m-1" />);
    expect(screen.getByRole('status')).toHaveTextContent(/not available/i);
    expect(screen.queryByText('No artifacts yet')).not.toBeInTheDocument();
  });

  test('makes no request to a route that does not exist', () => {
    const spy = vi.spyOn(globalThis, 'fetch');
    render(<ArtifactGallery orgId="org-1" />);
    expect(spy).not.toHaveBeenCalled();
  });
});
