"""每用户聊天限流（固定窗口，Redis 计数）。

防刷 LLM 预算：/chat/agent 每用户在 CHAT_RATE_LIMIT_WINDOW 秒内最多
CHAT_RATE_LIMIT_MAX 次。Redis 不可用/出错时放行——限流是成本保护而不是
可用性单点，与 metrics.py 同一套惰性连接 + 失败降级范式。
"""

from __future__ import annotations

import time

import redis

from ..core.config import CHAT_RATE_LIMIT_MAX, CHAT_RATE_LIMIT_WINDOW, REDIS_URL


class RateLimitExceeded(Exception):
    """窗口内请求超限，API 层捕获后转 HTTP 429。"""


class ChatRateLimiter:
    """固定窗口计数器，按 user_id 键控。

    _client 惰性创建并 ping 一次；连不上或后续任一命令异常都禁用（_disabled），
    禁用后放行所有请求，重启进程可恢复。
    """

    def __init__(
        self,
        redis_url: str = REDIS_URL,
        window: int = CHAT_RATE_LIMIT_WINDOW,
        limit: int = CHAT_RATE_LIMIT_MAX,
        client=None,
    ):
        self._redis_url = redis_url
        self._window = window
        self._limit = limit
        self._client = client  # 测试可注入 fake；生产为 None 时惰性连接
        self._disabled = False

    def _get(self) -> redis.Redis | None:
        if self._client is not None:
            return self._client
        if not self._disabled:
            try:
                client = redis.from_url(
                    self._redis_url,
                    decode_responses=True,
                    socket_connect_timeout=1.0,
                    socket_timeout=1.0,
                )
                client.ping()
                self._client = client
            except Exception:
                self._disabled = True
        return self._client

    def check(self, user_id: str) -> None:
        """窗口内超限抛 RateLimitExceeded；Redis 不可用/出错放行。"""
        client = self._get()
        if client is None:
            return
        try:
            window = int(time.time()) // self._window
            key = f"rag:ratelimit:chat:{user_id}:{window}"
            count = client.incr(key)
            if count == 1:
                client.expire(key, self._window + 5)
            if count > self._limit:
                raise RateLimitExceeded()
        except RateLimitExceeded:
            raise
        except Exception:
            pass  # Redis 出错不阻塞问答


# 模块级单例：路由直接 import，避免每请求重建连接。
chat_rate_limiter = ChatRateLimiter()


def check_chat_rate_limit(user_id: str) -> None:
    chat_rate_limiter.check(user_id)
