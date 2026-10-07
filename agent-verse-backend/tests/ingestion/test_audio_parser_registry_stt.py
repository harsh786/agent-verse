"""AudioParser transcribes with the Model Registry's speech-to-text model.

A real HTTP server on 127.0.0.1 stands in for an OpenAI-compatible
``/audio/transcriptions`` endpoint (only the network edge is replaced): the
parser must call the registry model at ITS ``base_url`` with ITS model id —
not ``OPENAI_BASE_URL`` + "whisper-1" — keep the segment timestamps, report which
model transcribed, and fail over to the next registry model when one is down.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import model_registry
from app.ingestion.parsers.audio_parser import AudioParser

_ISOLATE_PROVIDER_ENV = True

_TRANSCRIPT = {
    "text": "the quarterly numbers are up",
    "language": "en",
    "segments": [
        {"start": 0.0, "end": 1.5, "text": "the quarterly numbers"},
        {"start": 1.5, "end": 2.4, "text": "are up"},
    ],
}


class _Handler(BaseHTTPRequestHandler):
    requests: list[dict] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        type(self).requests.append(
            {"path": self.path, "body": body, "auth": self.headers.get("Authorization")}
        )
        payload = json.dumps(_TRANSCRIPT).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # keep test output quiet
        pass


@pytest.fixture
def stt_server():
    _Handler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1", _Handler.requests
    finally:
        server.shutdown()
        server.server_close()


def _dead_url() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}/v1"  # nothing listens here any more


@pytest.fixture(autouse=True)
def _registry(monkeypatch):
    import app.ai_router.selection as sel

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
    # A deployment-wide OpenAI endpoint that must NOT be used for transcription.
    monkeypatch.setenv("OPENAI_BASE_URL", _dead_url())
    monkeypatch.setattr("app.ai_router.speech.local_stt_available", lambda: False)
    model_registry.clear_configured()
    model_registry.set_preferences({})
    yield
    model_registry.clear_configured()
    model_registry.set_preferences({})


def _register(model_id: str, base_url: str) -> None:
    model_registry.register_configured(
        ModelEndpoint(
            provider="custom",
            model_id=model_id,
            display_name=model_id,
            capabilities=[ModelCapability.SPEECH_TO_TEXT],
            base_url=base_url,
            extra={"source": "override"},
        )
    )


async def test_audio_parser_uses_the_registry_stt_model_at_its_base_url(stt_server):
    base, requests = stt_server
    _register("Systran/faster-whisper-large-v3", base)

    result = await AudioParser().parse_bytes(
        b"RIFF-not-really-audio", "board-call.wav", "audio/wav"
    )

    assert result.error is None, result.error
    assert result.transcript == "the quarterly numbers are up"
    assert [(s.start, s.end) for s in result.segments] == [(0.0, 1.5), (1.5, 2.4)]
    assert result.model == "Systran/faster-whisper-large-v3"
    assert result.provider == "custom"
    assert result.model_source == "registry_cheapest"
    assert len(result.to_chunks(chunk_duration_seconds=1.0)) == 2

    assert len(requests) == 1
    sent = requests[0]
    assert sent["path"] == "/v1/audio/transcriptions"
    assert b"Systran/faster-whisper-large-v3" in sent["body"]
    assert b"whisper-1" not in sent["body"]
    assert b"verbose_json" in sent["body"]
    assert b"RIFF-not-really-audio" in sent["body"]


async def test_audio_parser_fails_over_to_the_next_registry_model(stt_server):
    base, requests = stt_server
    _register("primary-down", _dead_url())
    _register("secondary", base)
    model_registry.set_preferences({"speech_to_text": ["custom/primary-down", "custom/secondary"]})

    result = await AudioParser().parse_bytes(b"audio", "clip.mp3", "audio/mpeg")

    assert result.error is None, result.error
    assert result.model == "secondary"
    assert result.model_source == "registry_preference"
    assert len(requests) == 1 and b"secondary" in requests[0]["body"]


async def test_audio_parser_without_any_stt_model_reports_how_to_configure_one():
    result = await AudioParser().parse_bytes(b"audio", "clip.mp3", "audio/mpeg")
    assert result.transcript == ""
    assert result.error is not None
    assert "speech_to_text" in result.error and "Model Registry" in result.error
