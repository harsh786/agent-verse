"""KB-11: legacy connector ingest routes are bounded and honest about sync work.

* github/confluence/jira/slack declared 202 Accepted but did all the work
  synchronously inside the request.
* /ingest/notion paged a whole database and /ingest/gdrive-folder ingested every
  file, with no count/byte bound.
* Drive SDK calls ran on the event loop and the service-account key was written
  to a temp file.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.ingestion.connectors.gdrive_connector import GDriveConnector
from app.ingestion.connectors.notion_connector import NotionConnector
from tests.api.test_knowledge_coverage_boost import _auth, _client

_KEY_JSON = '{"type": "service_account", "client_email": "x@y.iam", "private_key": "k"}'


async def test_notion_list_pages_stops_at_the_page_cap() -> None:
    connector = NotionConnector(api_key="secret")
    calls = 0

    async def _post(path: str, body: dict[str, Any]) -> Any:
        nonlocal calls
        calls += 1
        return {
            "results": [{"id": f"p{calls}-{i}"} for i in range(body["page_size"])],
            "has_more": True,
            "next_cursor": f"c{calls}",
        }

    connector._post = _post  # type: ignore[method-assign]
    pages = await connector.list_pages("db", page_size=50, max_pages=120)
    assert len(pages) == 120
    assert calls == 3  # never paged past the cap


def test_notion_route_reports_truncation() -> None:
    connector = MagicMock()
    connector.list_pages = AsyncMock(return_value=[{"id": f"p{i}"} for i in range(5)])
    connector.fetch_page_content = AsyncMock(return_value="Some page content about releases.")
    orch = MagicMock()
    orch.ingest = AsyncMock(return_value=SimpleNamespace(chunks_created=1))
    with (
        patch(
            "app.ingestion.connectors.notion_connector.NotionConnector", return_value=connector
        ),
        patch("app.ingestion.orchestrator.IngestionOrchestrator", return_value=orch),
    ):
        resp = _client().post(
            "/knowledge/ingest/notion",
            json={"api_key": "k", "database_id": "db", "collection_id": "col-1", "max_pages": 5},
            headers=_auth(),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pages_ingested"] == 5
    assert body["truncated"] is True
    assert connector.list_pages.await_args.kwargs["max_pages"] == 5


def _drive_post(files: list[dict[str, str]], contents: dict[str, str], **extra: Any) -> Any:
    loop_thread = threading.get_ident()
    threads: list[int] = []
    connector = MagicMock()

    def _list(folder_id: str, **kw: Any) -> list[dict[str, str]]:
        threads.append(threading.get_ident())
        return files[: kw.get("max_files", len(files))]

    def _download(fid: str, mime: str, **kw: Any) -> str:
        threads.append(threading.get_ident())
        return contents[fid]

    connector.list_files.side_effect = _list
    connector.download_file.side_effect = _download
    orch = MagicMock()
    orch.ingest = AsyncMock(return_value=SimpleNamespace(chunks_created=1))
    with (
        patch(
            "app.ingestion.connectors.gdrive_connector.GDriveConnector", return_value=connector
        ) as cls,
        patch("app.ingestion.orchestrator.IngestionOrchestrator", return_value=orch),
        patch("tempfile.NamedTemporaryFile", side_effect=AssertionError("key written to disk")),
    ):
        resp = _client().post(
            "/knowledge/ingest/gdrive-folder",
            json={
                "folder_id": "f",
                "collection_id": "col-1",
                "service_account_key_json": _KEY_JSON,
                **extra,
            },
            headers=_auth(),
        )
    return resp, cls, threads, loop_thread


def test_drive_route_is_bounded_off_loop_and_keeps_the_key_in_memory() -> None:
    files = [{"id": f"f{i}", "name": f"n{i}.txt", "mimeType": "text/plain"} for i in range(10)]
    contents = {f["id"]: "x" * 400 for f in files}
    resp, cls, threads, loop_thread = _drive_post(
        files, contents, max_files=4, max_total_bytes=1000
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 4 files listed; the byte budget (1000) stops after 2 x 400 bytes + 1 over.
    assert body["files_ingested"] == 2
    assert body["truncated"] is True
    assert cls.call_args.kwargs.get("service_account_info", {}).get("type") == "service_account"
    assert "key_path" not in cls.call_args.kwargs
    assert threads and loop_thread not in threads


def test_gdrive_connector_builds_credentials_from_memory() -> None:
    import sys
    from types import ModuleType

    google = ModuleType("google")
    oauth2 = ModuleType("google.oauth2")
    service_account = ModuleType("google.oauth2.service_account")
    service_account.Credentials = MagicMock()  # type: ignore[attr-defined]
    service_account.Credentials.from_service_account_info.return_value = "creds"
    oauth2.service_account = service_account  # type: ignore[attr-defined]
    google.oauth2 = oauth2  # type: ignore[attr-defined]
    gac = ModuleType("googleapiclient")
    discovery = ModuleType("googleapiclient.discovery")
    discovery.build = MagicMock(return_value="svc")  # type: ignore[attr-defined]
    gac.discovery = discovery  # type: ignore[attr-defined]
    modules = {
        "google": google,
        "google.oauth2": oauth2,
        "google.oauth2.service_account": service_account,
        "googleapiclient": gac,
        "googleapiclient.discovery": discovery,
    }
    connector = GDriveConnector(service_account_info={"type": "service_account"})
    with patch.dict(sys.modules, modules):
        service = connector._build_service()
    service_account.Credentials.from_service_account_info.assert_called_once()
    assert service == "svc"


def test_sync_legacy_routes_answer_200_not_202() -> None:
    from app.api.knowledge import router

    paths = {"/knowledge/ingest/github", "/knowledge/ingest/confluence",
             "/knowledge/ingest/jira", "/knowledge/ingest/slack"}  # fmt: skip
    codes = {r.path: r.status_code for r in router.routes if getattr(r, "path", "") in paths}
    assert codes == dict.fromkeys(paths, 200)
