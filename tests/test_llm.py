"""AI provider layer: settings, validation, OpenAI-compatible calls (against a local fake server)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from cdp import config_store, llm
from cdp.config import ConfigError

SEEN: list[dict] = []


class _FakeChat(BaseHTTPRequestHandler):
    """Mimics POST /v1/chat/completions. Rejects response_format when ?strict-off is in the path."""

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        if "nojson" in self.path and "response_format" in body:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error": "response_format not supported"}')
            return
        reply = {"choices": [{"message": {"content": '```json\n{"ok": true}\n```'}}]}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(reply).encode())

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def server_url():
    server = HTTPServer(("127.0.0.1", 0), _FakeChat)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(autouse=True)
def clean_settings():
    yield
    llm.SETTINGS_PATH.unlink(missing_ok=True)


def test_compatible_provider_round_trip(server_url, monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "abc123")
    settings = {"provider": "openai_compatible", "model": "llama-test", "base_url": f"{server_url}/v1"}
    assert llm.is_configured(settings)
    llm.test_connection(settings)  # reply wrapped in a code fence is still parsed
    call = SEEN[-1]
    assert call["path"] == "/v1/chat/completions"
    assert call["auth"] == "Bearer abc123"
    assert call["body"]["model"] == "llama-test"
    assert call["body"]["response_format"] == {"type": "json_object"}
    assert call["body"]["messages"][0]["role"] == "system"


def test_falls_back_when_json_mode_unsupported(server_url):
    settings = {"provider": "openai_compatible", "model": "m", "base_url": f"{server_url}/nojson/v1"}
    llm.test_connection(settings)
    assert "response_format" in SEEN[-2]["body"] and "response_format" not in SEEN[-1]["body"]


def test_azure_url_and_header(server_url, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azkey")
    settings = {"provider": "azure_openai", "model": "gpt-deploy", "base_url": server_url}
    llm.test_connection(settings)
    assert SEEN[-1]["path"].startswith("/openai/deployments/gpt-deploy/chat/completions?api-version=")


def test_unconfigured_provider_is_reported(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="not configured"):
        llm.complete("s", [{"role": "user", "content": "hi"}], settings={"provider": "openai", "model": "x"})


def test_settings_are_validated_saved_and_loaded():
    with pytest.raises(ConfigError, match="base URL"):
        config_store.save_ai_settings({"provider": "azure_openai", "model": "d", "base_url": "not-a-url"})
    with pytest.raises(ConfigError, match="model"):
        config_store.save_ai_settings({"provider": "openai", "model": ""})
    config_store.save_ai_settings({"provider": "gemini", "model": "", "share_result_rows": False}, user="tester")
    saved = llm.load_settings()
    assert saved["provider"] == "gemini" and saved["share_result_rows"] is False
    assert llm.model_for(saved) == llm.PROVIDERS["gemini"]["default_model"]
    assert "key" not in json.dumps(saved).lower() or "api_key" not in saved


def test_parse_json_tolerates_wrapping():
    assert llm.parse_json('Sure! {"a": 1} hope that helps') == {"a": 1}
