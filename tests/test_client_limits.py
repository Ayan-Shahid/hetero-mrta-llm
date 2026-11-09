"""LLM client: daily quota, parsing robustness."""

import pytest

from mrta.llm.client import LLMConfig, QuotaExhausted, RateLimiter, parse_json


def test_parse_json_tolerates_reasoning_and_trailing_commas():
    assert parse_json('<think>{"x": 1}</think> here: {"a": [1, 2,],}') == {"a": [1, 2]}


def test_rate_limiter_stops_at_daily_quota(tmp_path):
    cfg = LLMConfig(provider="gemini", model="m", rpd=2)
    lim = RateLimiter(cfg, state_dir=tmp_path)
    lim.acquire()
    lim.acquire()
    with pytest.raises(QuotaExhausted):
        lim.acquire()
    assert RateLimiter(cfg, state_dir=tmp_path).used_today() == 2  # shared through the file


def test_cost_from_prices():
    cfg = LLMConfig(price_in=1.0, price_out=5.0)
    assert cfg.cost(1_000_000, 200_000) == pytest.approx(2.0)
