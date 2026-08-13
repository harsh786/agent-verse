"""SharePoint / OneDrive connector via Microsoft Graph API.

Authentication: OAuth 2.0 client credentials flow (app-only).
Requires an Azure AD app registration with Sites.Read.All permission.
"""
from __future__ import annotations

import json
from typing import Any

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
_SCOPE = "https://graph.microsoft.com/.default"


class SharePointConnector:
    """Read files from SharePoint sites and OneDrive via Microsoft Graph API.

    Parameters
    ----------
    tenant_id : str
        Azure AD tenant ID (GUID or domain).
    client_id : str
        App registration client ID.
    client_secret : str
        App registration client secret.
    """

    def __init__(self, tenant_id: str, client_id: str, client_secret: str) -> None:
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token: str | None = None

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def get_access_token(self) -> str:
        """Obtain an OAuth 2.0 access token using client credentials."""
        import httpx

        url = _TOKEN_URL.format(tenant_id=self._tenant_id)
        data = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "scope": _SCOPE,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, data=data)
            resp.raise_for_status()
            payload = resp.json()
            self._access_token = payload["access_token"]
            return self._access_token

    async def _headers(self) -> dict[str, str]:
        if not self._access_token:
            await self.get_access_token()
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        import httpx

        url = f"{_GRAPH_BASE}/{path.lstrip('/')}"
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers, params=params or {})
            if resp.status_code == 401:
                # Retry once with fresh token
                await self.get_access_token()
                headers = await self._headers()
                resp = await client.get(url, headers=headers, params=params or {})
            resp.raise_for_status()
            return resp.json()

    async def _download(self, download_url: str) -> bytes:
        import httpx

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(download_url)
            resp.raise_for_status()
            return resp.content

    # ------------------------------------------------------------------
    # Sites
    # ------------------------------------------------------------------

    async def list_sites(self, search: str = "*") -> list[dict[str, Any]]:
        """List SharePoint sites accessible to the app.

        Parameters
        ----------
        search : str
            Search query for site name/URL. Use "*" to list all.
        """
        data = await self._get("sites", params={"search": search})
        return data.get("value", [])

    async def get_site(self, site_id: str) -> dict[str, Any]:
        """Get metadata for a specific SharePoint site."""
        return await self._get(f"sites/{site_id}")

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    async def list_files(
        self,
        site_id: str,
        drive_id: str | None = None,
        folder_path: str = "root",
    ) -> list[dict[str, Any]]:
        """List files in a SharePoint document library.

        Parameters
        ----------
        site_id : str
            SharePoint site ID.
        drive_id : str | None
            Drive (document library) ID. If None, uses the default drive.
        folder_path : str
            Folder path relative to drive root. "root" lists root-level items.
        """
        if drive_id:
            path = f"sites/{site_id}/drives/{drive_id}/{folder_path}/children"
        else:
            path = f"sites/{site_id}/drive/{folder_path}/children"
        data = await self._get(path, params={"$select": "id,name,file,folder,size,webUrl,lastModifiedDateTime"})
        items = data.get("value", [])
        # Return only files (not folders)
        return [item for item in items if "file" in item]

    async def list_all_files(
        self,
        site_id: str,
        drive_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Recursively list all files in a drive."""
        files: list[dict[str, Any]] = []
        # Start from root
        root_files = await self.list_files(site_id, drive_id, "root")
        files.extend(root_files)
        return files

    async def download_file(self, site_id: str, item_id: str) -> str:
        """Download file content as a UTF-8 string.

        Parameters
        ----------
        site_id : str
            SharePoint site ID.
        item_id : str
            DriveItem ID of the file.
        """
        # Get download URL first
        meta = await self._get(f"sites/{site_id}/drive/items/{item_id}")
        download_url = meta.get("@microsoft.graph.downloadUrl", "")
        if download_url:
            content_bytes = await self._download(download_url)
            return content_bytes.decode("utf-8", errors="replace")
        return ""

    async def list_drives(self, site_id: str) -> list[dict[str, Any]]:
        """List all document libraries (drives) for a site."""
        data = await self._get(f"sites/{site_id}/drives")
        return data.get("value", [])

    async def get_file_metadata(self, site_id: str, item_id: str) -> dict[str, Any]:
        """Return metadata for a single file."""
        return await self._get(f"sites/{site_id}/drive/items/{item_id}")
