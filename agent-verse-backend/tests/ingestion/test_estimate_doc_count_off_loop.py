"""S3-ESTIMATE-BLOCKING: ``estimate_doc_count`` never runs on the event loop.

``S3Connector.estimate_doc_count`` is a synchronous boto3 ``ListObjectsV2`` call
(the method is sync by contract, for progress estimates). No async code calls it
today; the hazard is the next caller doing so from a coroutine. Async code uses
``BaseConnector.estimate_doc_count_async``, which runs it on the SDK pool, and a
source scan keeps any coroutine in ``app/`` from calling the sync method directly.
"""

from __future__ import annotations

import ast
import asyncio
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from app.ingestion.connectors.s3_connector import S3Connector
from app.ingestion.source_config import SourceConfig, SourceFamily

APP = Path(__file__).resolve().parents[2] / "app"


def _config() -> SourceConfig:
    return SourceConfig(
        source_id="src-s3",
        tenant_id="t1",
        name="s3",
        family=SourceFamily.OBJECT_STORAGE,
        source_type="s3",
        connection_config={"bucket": "b"},
    )


async def test_estimate_doc_count_async_does_not_block_the_loop() -> None:
    s3 = MagicMock()

    def _slow_list(**_k: Any) -> dict[str, int]:
        time.sleep(0.25)
        return {"KeyCount": 7}

    s3.list_objects_v2.side_effect = _slow_list
    lags: list[float] = []
    done = asyncio.Event()

    async def _probe() -> None:
        loop = asyncio.get_running_loop()
        while not done.is_set():
            start = loop.time()
            await asyncio.sleep(0.01)
            lags.append(loop.time() - start - 0.01)

    with patch("boto3.client", return_value=s3):
        probe = asyncio.create_task(_probe())
        await asyncio.sleep(0)
        count = await S3Connector().estimate_doc_count_async(_config())
        done.set()
        await probe
    assert count == 7
    assert max(lags) < 0.1, f"event loop blocked for {max(lags):.3f}s"


async def test_the_base_default_is_none() -> None:
    from app.ingestion.connectors.rss_connector import RSSConnector

    assert await RSSConnector().estimate_doc_count_async(_config()) is None


def test_no_coroutine_in_app_calls_the_blocking_estimate_directly() -> None:
    offenders: list[str] = []
    for path in APP.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if ".estimate_doc_count(" not in source:
            continue  # cheap pre-filter: parse only files that mention the call
        tree = ast.parse(source)
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "estimate_doc_count"
                ):
                    offenders.append(f"{path.relative_to(APP)}:{node.lineno} in {fn.name}")
    assert not offenders, f"use estimate_doc_count_async in async code: {offenders}"
