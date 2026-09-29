/**
 * ArtifactGallery — mission outputs panel on the mission page.
 *
 * The backend has no org/mission artifact store: GET /v1/org/{org_id}/artifacts
 * does not exist. This component used to call it and swallow the 404 into an
 * empty list, so every mission claimed "No artifacts yet" — indistinguishable
 * from a mission that genuinely produced nothing. Until a real endpoint
 * exists it states plainly that artifacts are not available, and makes no
 * request.
 */
import { FileText } from 'lucide-react';

interface ArtifactGalleryProps {
  orgId: string;
  missionId?: string;
}

export function ArtifactGallery(_props: ArtifactGalleryProps) {
  return (
    <div className="space-y-4" role="region" aria-label="Artifact gallery">
      <div
        role="status"
        className="flex flex-col items-center justify-center h-32 border border-dashed border-[var(--border)] rounded-xl text-center px-4"
      >
        <FileText className="h-8 w-8 text-[var(--text-muted)] mb-2" aria-hidden="true" />
        <p className="text-sm text-[var(--text-muted)]">Mission artifacts are not available</p>
        <p className="text-xs text-[var(--text-muted)]">
          This server does not store versioned mission artifacts yet. Task outputs are shown on
          each task.
        </p>
      </div>
    </div>
  );
}
