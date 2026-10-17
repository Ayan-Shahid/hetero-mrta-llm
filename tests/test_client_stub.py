"""The real API client, tested against a local OpenAI-compatible stub server (no internet needed)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from mrta.llm import LLM_METHODS
from mrta.llm.client import LLMConfig, OpenAICompatClient
from mrta.scenarios import generate, render


class _Stub(BaseHTTPRequestHandler):
    requests: list = []

    def log_message(self, *args):  # keep test output clean
        pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        _Stub.requests.append(req)
        if "response_format" in req:  # behave like a provider without JSON mode
            self._send(400, {"error": {"message": "response_format is not supported", "type": "invalid_request_error"}})
            return
        content = 'Sure:\n{"notes": "stub", "standby": [], "routes": {"D1": ["T1"]}}'
        self._send(200, {"id": "x", "object": "chat.completion", "created": 0, "model": req["model"],
                         "choices": [{"index": 0, "finish_reason": "stop",
                                      "message": {"role": "assistant", "content": content}}],
                         "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    httpd = HTTPServer(("127.0.0.1", 0), _Stub)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    _Stub.requests = []
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


def test_json_mode_fallback_usage_and_cache(server, tmp_path):
    cfg = LLMConfig(provider="lmstudio", base_url=server, model="stub", cache_dir=str(tmp_path), max_retries=0)
    client = OpenAICompatClient(cfg)
    scn = generate("S", 0)
    first = LLM_METHODS["llm-direct"](client, scn, render(scn, "explicit"), "explicit")
    assert first.status == "ok" and first.plan.routes == {"D1": ["T1"]}
    assert (first.prompt_tokens, first.completion_tokens) == (100, 20)
    assert client._json_mode is False  # learned that the provider rejects response_format
    n = len(_Stub.requests)
    second = LLM_METHODS["llm-direct"](client, scn, render(scn, "explicit"), "explicit")
    assert second.trace[-1]["cached"] and len(_Stub.requests) == n  # served from the disk cache
