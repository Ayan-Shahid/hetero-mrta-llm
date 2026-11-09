"""One chat interface for every LLM provider.

All providers listed in PROVIDERS speak the OpenAI chat-completions protocol, so a
single client covers OpenAI, Google Gemini, Anthropic (Claude), Groq, OpenRouter,
DeepSeek and local models served by Ollama or LM Studio.  Switch provider by
editing one line in the YAML config.

Phase 2 additions
-----------------
* Rate limiting: ``rpm`` (requests per minute) and ``rpd`` (requests per day) are
  enforced across processes with a small counter file, so a free-tier key is never
  pushed past its quota.  When the daily quota is used up the client raises
  :class:`QuotaExhausted`; experiment runners stop cleanly and ``--resume`` continues
  the next day.
* Repeats: ``seed`` is sent to the API (when supported) and is part of the cache key,
  so three repeats at temperature > 0 are three genuinely different samples.
* Cost: ``price_in`` / ``price_out`` (USD per million tokens) give a cost per call.
* Reasoning models: ``<think>...</think>`` blocks are stripped from replies.

    python -m mrta.llm --config configs/models/gemini-flash.yaml --ping
    python -m mrta.llm --config configs/models/gemini-flash.yaml --list-models
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

PROVIDERS: dict[str, tuple[str, str | None]] = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY"),
    "anthropic": ("https://api.anthropic.com/v1/", "ANTHROPIC_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "deepseek": ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    "ollama": ("http://localhost:11434/v1", None),
    "lmstudio": ("http://localhost:1234/v1", None),
}

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


class QuotaExhausted(RuntimeError):
    """The provider's daily request quota (configured ``rpd`` or reported by the API) is used up."""


@dataclass
class LLMConfig:
    provider: str = "mock"
    model: str = "mock"
    base_url: str | None = None  # overrides the provider preset
    api_key_env: str | None = None  # overrides the provider preset
    temperature: float | None = 0.0  # None = do not send (some models reject it)
    max_tokens: int | None = 4096
    json_mode: bool = True  # ask for response_format=json_object when the provider supports it
    timeout_s: float = 180.0
    max_retries: int = 5
    cache_dir: str | None = ".cache/llm"
    extra: dict[str, Any] = field(default_factory=dict)  # passed straight to the API
    seed: int | None = None  # sent to the API when supported; part of the cache key
    rpm: float | None = None  # max requests per minute (None = unlimited)
    rpd: int | None = None  # max requests per day, counted across processes (None = unlimited)
    price_in: float = 0.0  # USD per million prompt tokens
    price_out: float = 0.0  # USD per million completion tokens
    label: str | None = None  # short name used in result files (default provider:model)
    mock_error_rate: float = 0.15  # only used by provider: mock

    @classmethod
    def load(cls, path: str | Path, **overrides: Any) -> "LLMConfig":
        data = yaml.safe_load(Path(path).read_text()) or {}
        data.update({k: v for k, v in overrides.items() if v is not None})
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    @property
    def name(self) -> str:
        return self.label or f"{self.provider}:{self.model}"

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (prompt_tokens * self.price_in + completion_tokens * self.price_out) / 1e6


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


# ----------------------------------------------------------------------------- rate limiting
class RateLimiter:
    """Requests-per-minute pacing plus a per-day counter shared through a JSON file.

    The day counter resets at midnight Pacific time, which is when Google resets Gemini
    free-tier quotas; for other providers the exact reset time does not matter.
    """

    _lock = threading.Lock()

    def __init__(self, cfg: LLMConfig, state_dir: str | Path = ".cache/ratelimit"):
        self.rpm, self.rpd = cfg.rpm, cfg.rpd
        key = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{cfg.provider}_{cfg.model}")
        self.path = Path(state_dir) / f"{key}.json"
        self._recent: list[float] = []

    @staticmethod
    def _today() -> str:
        pacific = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=8)
        return pacific.strftime("%Y-%m-%d")

    def _read(self) -> dict[str, int]:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def used_today(self) -> int:
        return int(self._read().get(self._today(), 0))

    def acquire(self) -> None:
        with self._lock:
            if self.rpd is not None:
                counts = self._read()
                today = self._today()
                if counts.get(today, 0) >= self.rpd:
                    raise QuotaExhausted(f"daily limit of {self.rpd} requests reached for {self.path.stem}; "
                                         "re-run with --resume after the quota resets")
                counts = {today: counts.get(today, 0) + 1}
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(counts))
            if self.rpm:
                window = 60.0
                now = time.monotonic()
                self._recent = [t for t in self._recent if now - t < window]
                if len(self._recent) >= self.rpm:
                    time.sleep(window - (now - self._recent[0]) + 0.05)
                self._recent.append(time.monotonic())


def _is_daily_quota(msg: str) -> bool:
    msg = msg.lower()
    return ("per day" in msg or "perday" in msg or "daily" in msg) and ("quota" in msg or "limit" in msg)


def _retry_after(err: Exception) -> float | None:
    resp = getattr(err, "response", None)
    headers = getattr(resp, "headers", None) or {}
    for key in ("retry-after", "Retry-After"):
        if key in headers:
            try:
                return float(headers[key])
            except (TypeError, ValueError):
                pass
    m = re.search(r"retry in ([0-9.]+)\s*s", str(err), re.I)
    return float(m.group(1)) if m else None


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
            raise RuntimeError(f"environment variable {key_env} is not set (API key for {config.provider}); "
                               "put it in .env or export it")
        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=config.timeout_s, max_retries=0)
        self.limiter = RateLimiter(config)
        self._json_mode = config.json_mode
        self._send_temperature = config.temperature is not None
        self._send_seed = config.seed is not None
        self._max_tokens_key = "max_tokens"
        self._extra = dict(config.extra)

    # cache ------------------------------------------------------------------
    def _cache_path(self, messages: list[dict[str, str]]) -> Path | None:
        if not self.config.cache_dir:
            return None
        blob = json.dumps([self.config.provider, self.config.model, self.config.temperature,
                           self.config.extra, self.config.seed, messages], sort_keys=True)
        digest = hashlib.sha256(blob.encode()).hexdigest()[:24]
        return Path(self.config.cache_dir) / f"{digest}.json"

    def _kwargs(self, messages) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": self.config.model, "messages": messages}
        if self._send_temperature:
            kwargs["temperature"] = self.config.temperature
        if self._send_seed:
            kwargs["seed"] = self.config.seed
        if self.config.max_tokens:
            kwargs[self._max_tokens_key] = self.config.max_tokens
        if self._json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if self._extra:
            kwargs["extra_body"] = dict(self._extra)
        return kwargs

    def _adapt(self, msg: str) -> bool:
        """Drop one parameter the provider rejected. Returns False when nothing is left to drop."""
        msg = msg.lower()
        if self._json_mode and ("response_format" in msg or "json" in msg):
            self._json_mode = False
            return True
        if self._send_temperature and "temperature" in msg:
            self._send_temperature = False
            return True
        if self._send_seed and "seed" in msg:
            self._send_seed = False
            return True
        if self._max_tokens_key == "max_tokens" and "max_tokens" in msg:
            self._max_tokens_key = "max_completion_tokens"
            return True
        for key in list(self._extra):
            if key.lower() in msg:
                del self._extra[key]
                return True
        return False

    def chat(self, messages, *, purpose, context=None) -> LLMResponse:
        from openai import (APIConnectionError, APITimeoutError, BadRequestError, InternalServerError,
                            RateLimitError)

        path = self._cache_path(messages)
        if path and path.exists():
            hit = json.loads(path.read_text())
            return LLMResponse(hit["text"], hit["prompt_tokens"], hit["completion_tokens"], hit["latency_s"], True)

        delay, retries = 2.0, 0
        while True:
            self.limiter.acquire()
            try:
                t0 = time.perf_counter()
                resp = self.client.chat.completions.create(**self._kwargs(messages))
                latency = time.perf_counter() - t0
            except BadRequestError as err:  # adapt once to each known provider quirk, then give up
                if self._adapt(str(err)):
                    continue
                raise
            except RateLimitError as err:
                if _is_daily_quota(str(err)):
                    raise QuotaExhausted(str(err)[:300]) from err
                if retries >= self.config.max_retries:
                    raise
                retries += 1
                wait = _retry_after(err) or delay
                time.sleep(min(wait, 120))
                delay = min(delay * 2, 60)
                continue
            except (APIConnectionError, APITimeoutError, InternalServerError):
                if retries >= self.config.max_retries:
                    raise
                retries += 1
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            text = _THINK.sub("", resp.choices[0].message.content or "").strip()
            usage = getattr(resp, "usage", None)
            out = LLMResponse(text=text,
                              prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                              completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                              latency_s=latency)
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"purpose": purpose, **out.__dict__, "cached": False}))
            return out


def make_client(config: LLMConfig | str | Path | None, **overrides: Any) -> ChatClient:
    """Build a client from a config object, a YAML path, or the strings 'mock' / 'mock-oracle'."""
    if config is None or (isinstance(config, str) and config in ("mock", "mock-oracle")):
        from .mock import MockLLM

        rate = 0.0 if config == "mock-oracle" else LLMConfig().mock_error_rate
        cfg = LLMConfig(provider="mock", model=str(config or "mock"), mock_error_rate=rate)
        for k, v in overrides.items():
            if v is not None and hasattr(cfg, k):
                setattr(cfg, k, v)
        return MockLLM(cfg)
    if not isinstance(config, LLMConfig):
        config = LLMConfig.load(config, **overrides)
    if config.provider == "mock":
        from .mock import MockLLM

        return MockLLM(config)
    return OpenAICompatClient(config)


def parse_json(text: str) -> Any:
    """Parse the first JSON object in an LLM reply (tolerates code fences, chatter and trailing commas)."""
    text = _THINK.sub("", text).strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    for candidate in (text, re.sub(r",\s*([}\]])", r"\1", text)):
        try:
            return json.loads(candidate)
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
                    chunk = text[start:i + 1]
                    for candidate in (chunk, re.sub(r",\s*([}\]])", r"\1", chunk)):
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            pass
                    break
        start = text.find("{", start + 1)
    raise ValueError("no JSON object found in the model reply")


def load_dotenv(path: str | Path = ".env") -> None:
    """Read KEY=value lines from .env into the environment (existing variables win)."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _main() -> None:
    ap = argparse.ArgumentParser(description="Check an LLM provider configuration")
    ap.add_argument("--config", default="configs/models/gemini-flash.yaml")
    ap.add_argument("--ping", action="store_true", help="send one tiny request")
    ap.add_argument("--list-models", action="store_true")
    args = ap.parse_args()
    load_dotenv()
    try:
        cfg = LLMConfig.load(args.config)
        cfg.cache_dir = None
        client = make_client(cfg)
        if args.list_models:
            if not isinstance(client, OpenAICompatClient):
                print("mock provider: no models to list")
                return
            for m in client.client.models.list():
                print(m.id)
        if args.ping or not args.list_models:
            reply = client.chat([{"role": "system", "content": 'Reply with JSON {"ok": true}.'},
                                 {"role": "user", "content": "ping"}], purpose="ping")
            print(f"{cfg.name} replied in {reply.latency_s:.2f}s: {reply.text[:200]!r}")
            if isinstance(client, OpenAICompatClient) and cfg.rpd:
                print(f"requests counted today: {client.limiter.used_today()} of {cfg.rpd}")
    except Exception as err:  # noqa: BLE001 - a setup check should explain, not dump a traceback
        raise SystemExit(f"LLM check failed: {type(err).__name__}: {err}")


if __name__ == "__main__":
    _main()
