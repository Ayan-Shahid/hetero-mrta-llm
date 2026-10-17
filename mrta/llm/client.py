"""One chat interface for every LLM provider.

All providers listed in PROVIDERS speak the OpenAI chat-completions protocol, so a
single client covers OpenAI, Google Gemini, Groq, OpenRouter, DeepSeek and local
models served by Ollama or LM Studio.  Switch provider by editing one line in
configs/llm.yaml.  Responses are cached on disk (keyed by model + messages), so
re-running an experiment does not spend tokens twice.

    python -m mrta.llm --config configs/llm.yaml --ping         # test the key
    python -m mrta.llm --config configs/llm.yaml --list-models  # models your key can use
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

PROVIDERS: dict[str, tuple[str, str | None]] = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "deepseek": ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    "ollama": ("http://localhost:11434/v1", None),
    "lmstudio": ("http://localhost:1234/v1", None),
}


@dataclass
class LLMConfig:
    provider: str = "mock"
    model: str = "mock"
    base_url: str | None = None  # overrides the provider preset
    api_key_env: str | None = None  # overrides the provider preset
    temperature: float | None = 0.0  # None = do not send (some reasoning models reject it)
    max_tokens: int | None = 4096
    json_mode: bool = True  # ask for response_format=json_object when the provider supports it
    timeout_s: float = 180.0
    max_retries: int = 5
    cache_dir: str | None = ".cache/llm"
    extra: dict[str, Any] = field(default_factory=dict)  # passed straight to the API
    mock_error_rate: float = 0.15  # only used by provider: mock

    @classmethod
    def load(cls, path: str | Path) -> "LLMConfig":
        data = yaml.safe_load(Path(path).read_text()) or {}
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    cached: bool = False


class ChatClient(Protocol):
    config: LLMConfig

    def chat(self, messages: list[dict[str, str]], *, purpose: str,
             context: dict[str, Any] | None = None) -> LLMResponse: ...


class OpenAICompatClient:
    def __init__(self, config: LLMConfig):
        from openai import OpenAI

        self.config = config
        preset_url, preset_key = PROVIDERS.get(config.provider, (None, None))
        base_url = config.base_url or preset_url
        if base_url is None:
            raise ValueError(f"unknown provider '{config.provider}'; set base_url in the config")
        key_env = config.api_key_env or preset_key
        api_key = os.environ.get(key_env, "") if key_env else "not-needed"
        if key_env and not api_key:
            raise RuntimeError(f"environment variable {key_env} is not set (API key for {config.provider})")
        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=config.timeout_s, max_retries=0)
        self._json_mode = config.json_mode
        self._send_temperature = config.temperature is not None
        self._max_tokens_key = "max_tokens"

    # cache ------------------------------------------------------------------
    def _cache_path(self, messages: list[dict[str, str]]) -> Path | None:
        if not self.config.cache_dir:
            return None
        blob = json.dumps([self.config.provider, self.config.model, self.config.temperature,
                           self.config.extra, messages], sort_keys=True)
        digest = hashlib.sha256(blob.encode()).hexdigest()[:24]
        return Path(self.config.cache_dir) / f"{digest}.json"

    def chat(self, messages, *, purpose, context=None) -> LLMResponse:
        from openai import (APIConnectionError, APITimeoutError, BadRequestError, InternalServerError,
                            RateLimitError)

        path = self._cache_path(messages)
        if path and path.exists():
            hit = json.loads(path.read_text())
            return LLMResponse(hit["text"], hit["prompt_tokens"], hit["completion_tokens"], hit["latency_s"], True)

        delay, retries = 2.0, 0
        while True:
            kwargs: dict[str, Any] = {"model": self.config.model, "messages": messages}
            if self._send_temperature:
                kwargs["temperature"] = self.config.temperature
            if self.config.max_tokens:
                kwargs[self._max_tokens_key] = self.config.max_tokens
            if self._json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            kwargs.update(self.config.extra)
            try:
                t0 = time.perf_counter()
                resp = self.client.chat.completions.create(**kwargs)
                latency = time.perf_counter() - t0
            except BadRequestError as err:  # adapt once to each known provider quirk, then give up
                msg = str(err).lower()
                if self._json_mode and ("response_format" in msg or "json" in msg):
                    self._json_mode = False
                    continue
                if self._send_temperature and "temperature" in msg:
                    self._send_temperature = False
                    continue
                if self._max_tokens_key == "max_tokens" and "max_tokens" in msg:
                    self._max_tokens_key = "max_completion_tokens"
                    continue
                raise
            except (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError):
                if retries >= self.config.max_retries:
                    raise
                retries += 1
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            text = resp.choices[0].message.content or ""
            usage = getattr(resp, "usage", None)
            out = LLMResponse(text=text,
                              prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                              completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                              latency_s=latency)
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"purpose": purpose, **out.__dict__, "cached": False}))
            return out


def make_client(config: LLMConfig | str | Path | None) -> ChatClient:
    """Build a client from a config object, a YAML path, or the strings 'mock' / 'mock-oracle'."""
    if config is None or (isinstance(config, str) and config in ("mock", "mock-oracle")):
        from .mock import MockLLM

        rate = 0.0 if config == "mock-oracle" else LLMConfig().mock_error_rate
        return MockLLM(LLMConfig(provider="mock", model=str(config or "mock"), mock_error_rate=rate))
    if not isinstance(config, LLMConfig):
        config = LLMConfig.load(config)
    if config.provider == "mock":
        from .mock import MockLLM

        return MockLLM(config)
    return OpenAICompatClient(config)


def parse_json(text: str) -> Any:
    """Parse the first JSON object in an LLM reply (tolerates code fences and chatter)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise ValueError("no JSON object found in the model reply")


def _main() -> None:
    ap = argparse.ArgumentParser(description="Check an LLM provider configuration")
    ap.add_argument("--config", default="configs/llm.yaml")
    ap.add_argument("--ping", action="store_true", help="send one tiny request")
    ap.add_argument("--list-models", action="store_true")
    args = ap.parse_args()
    try:
        cfg = LLMConfig.load(args.config)
        client = make_client(cfg)
        if args.list_models:
            if not isinstance(client, OpenAICompatClient):
                print("mock provider: no models to list")
                return
            for m in client.client.models.list():
                print(m.id)
        if args.ping or not args.list_models:
            cfg.cache_dir = None
            reply = client.chat([{"role": "system", "content": 'Reply with JSON {"ok": true}.'},
                                 {"role": "user", "content": "ping"}], purpose="ping")
            print(f"{cfg.label} replied in {reply.latency_s:.2f}s: {reply.text[:200]!r}")
    except Exception as err:  # noqa: BLE001 - a setup check should explain, not dump a traceback
        raise SystemExit(f"LLM check failed: {type(err).__name__}: {err}")


if __name__ == "__main__":
    _main()
