"""聊天限流 + 问答 LLM 超时/重试配置测试。

限流用 fake Redis 注入：窗口内计数、超限抛 RateLimitExceeded、窗口翻转归零、
Redis 故障放行、每用户独立计数。LLM 客户端用 monkeypatch 断言
timeout/max_retries 真实传给 ChatOpenAI。
"""

import pytest

from backend.app.api.rate_limit import ChatRateLimiter, RateLimitExceeded


class _FakeRedis:
    def __init__(self):
        self.counts = {}
        self.expired = {}

    def ping(self):
        return True

    def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def expire(self, key, ttl):
        self.expired[key] = ttl


def test_under_limit_passes():
    limiter = ChatRateLimiter(window=60, limit=12, client=_FakeRedis())
    for _ in range(12):
        limiter.check("user_1")  # 正好在限内，不应抛


def test_over_limit_raises():
    limiter = ChatRateLimiter(window=60, limit=12, client=_FakeRedis())
    with pytest.raises(RateLimitExceeded):
        for _ in range(13):
            limiter.check("user_1")


def test_window_rollover_resets_count(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr("backend.app.api.rate_limit.time.time", lambda: clock["t"])
    limiter = ChatRateLimiter(window=60, limit=2, client=_FakeRedis())
    limiter.check("user_1")
    limiter.check("user_1")
    with pytest.raises(RateLimitExceeded):
        limiter.check("user_1")
    # 进入下一窗口 → 计数归零，不再超限
    clock["t"] = 200.0
    limiter.check("user_1")


def test_keys_are_per_user_and_set_expiry(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr("backend.app.api.rate_limit.time.time", lambda: clock["t"])
    fake = _FakeRedis()
    limiter = ChatRateLimiter(window=60, limit=1, client=fake)
    limiter.check("alice")
    with pytest.raises(RateLimitExceeded):
        limiter.check("alice")
    limiter.check("bob")  # 独立计数，不受 alice 影响
    assert fake.expired  # 首次 incr 设置了过期，避免窗口 key 永驻


def test_redis_failure_fails_open():
    class _Boom:
        def ping(self):
            raise ConnectionError("redis down")

        def incr(self, key):
            raise ConnectionError("redis down")

    limiter = ChatRateLimiter(client=_Boom())
    limiter.check("user_1")  # Redis 不可用 → 放行，不抛异常


def test_agent_llm_sets_timeout_and_retries(monkeypatch):
    """超时与重试是防挂死/防瞬时失败的关键参数，必须真实传给 ChatOpenAI。"""
    import backend.app.api.routes_retrieval as rr

    captured = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def with_config(self, **kwargs):
            return self

        def bind_tools(self, *args, **kwargs):
            return self

    monkeypatch.setattr(rr, "ChatOpenAI", _FakeOpenAI)
    monkeypatch.setattr(
        rr,
        "get_model_config",
        lambda model_id: {"base_url": "https://fake", "api_key": "k", "ready": True},
    )
    monkeypatch.delitem(rr._AGENT_LLMS, "gpt-5.6-luna", raising=False)
    rr._get_agent_llm("gpt-5.6-luna")
    assert captured["timeout"] == rr.LLM_REQUEST_TIMEOUT
    assert captured["max_retries"] == rr.LLM_MAX_RETRIES
