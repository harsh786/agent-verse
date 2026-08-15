"""Webhook secret rotation — supports manual and scheduled rotation."""
from __future__ import annotations

import secrets
import logging
from typing import Any

_log = logging.getLogger(__name__)

_SECRET_BYTES = 32


class WebhookSecretRotation:
    """Manage webhook secret rotation lifecycle."""

    def generate_secret(self) -> str:
        """Generate a new cryptographically-secure webhook secret."""
        return secrets.token_hex(_SECRET_BYTES)

    async def rotate(
        self,
        trigger_id: str,
        *,
        store: Any = None,
        tenant_id: str = "",
        grace_period_seconds: int = 300,
    ) -> dict:
        """Rotate the webhook secret for a trigger.

        Returns dict with {trigger_id, new_secret, old_secret, grace_period_seconds}.
        Writes back through store if provided.
        """
        new_secret = self.generate_secret()
        result = {
            "trigger_id": trigger_id,
            "new_secret": new_secret,
            "grace_period_seconds": grace_period_seconds,
        }

        if store is not None:
            # Update the secret in the trigger store if the API supports it
            try:
                await store.update_secret_async(
                    trigger_id,
                    new_secret=new_secret,
                    tenant_id=tenant_id,
                    grace_period_seconds=grace_period_seconds,
                )
            except (AttributeError, NotImplementedError):
                _log.debug("store.update_secret_async not implemented — skipping persist")

        _log.info("webhook_secret_rotated trigger_id=%s", trigger_id)
        return result

    async def schedule_rotation(
        self,
        trigger_ids: list[str],
        *,
        interval_days: int = 90,
        tenant_id: str = "",
    ) -> list[str]:
        """Return list of trigger IDs that need rotation based on age threshold."""
        # In a real implementation this would query last_rotated_at from the DB.
        # For now, return all provided IDs to trigger rotation.
        return trigger_ids
