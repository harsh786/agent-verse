"""Shared fixtures for SDK unit tests using RESPX to mock HTTP."""
from __future__ import annotations

import pytest
import respx
from agentverse.client import AgentVerseClient

BASE_URL = "http://localhost:8000"
API_KEY = "test-key-123"


@pytest.fixture
def mock_http():
    """Activates a RESPX mock router scoped to the test."""
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def client(mock_http):
    """Yields a live AgentVerseClient backed by RESPX mocks."""
    async with AgentVerseClient(api_key=API_KEY, base_url=BASE_URL) as c:
        yield c
