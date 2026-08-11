"""运行指标（Phase 2.1）：Redis 计数/延迟采样，Redis 不可用时静默降级。

worker（解析/入库/OCR）与 API（问答/成本）共用同一 Redis，指标在此聚合，
/admin/metrics 读快照。任何 Redis 异常都吞掉并标记禁用，绝不阻塞业务链路。

命名空间：
  rag:metrics:counter:{name}  整数计数器（incr，如 docs_ingested / tokens_input）
  rag:metrics:latency:{name}  最近 N 个延迟采样（毫秒，observe → LPUSH+LTRIM 定界）
"""

from __future__ import annotations

import redis

from ..core.config import REDIS_URL

# 每个延迟指标保留的最近采样数；超过丢最老（均值/分位数都是滑窗口径）
MAX_SAMPLES = 500


class MetricsStore:
    """Redis 计数的轻量封装。

    _client 惰性创建并 ping 一次；连不上或后续任一命令异常都禁用（_disabled），
    避免每个请求都重连。禁用后重启进程可恢复。
    """

    def __init__(
        self,
        redis_url: str = REDIS_URL,
        max_samples: int = MAX_SAMPLES,
        client=None,
    ):
        self._redis_url = redis_url
        self._max_samples = max_samples
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

    # ---------- 写入 ----------

    def incr(self, name: str, n: int = 1) -> None:
        client = self._get()
        if client is None:
            return
        try:
            client.incrby(self._key("counter", name), n)
        except Exception:
            pass

    def observe(self, name: str, value_ms: float) -> None:
        """记录一次延迟采样（毫秒），保留最近 max_samples 条。"""
        client = self._get()
        if client is None:
            return
        try:
            key = self._key("latency", name)
            pipe = client.pipeline()
            pipe.lpush(key, float(value_ms))
            pipe.ltrim(key, 0, self._max_samples - 1)
            pipe.execute()
        except Exception:
            pass

    # ---------- 读取 ----------

    def snapshot(self) -> dict:
        """读取全部指标：计数器 + 延迟（滑窗 count/avg/p50/p95/min/max）。"""
        client = self._get()
        result = {"counters": {}, "latencies": {}}
        if client is None:
            return result
        try:
            cursor = 0
            while True:
                cursor, keys = client.scan(cursor, match="rag:metrics:*", count=500)
                for key in keys:
                    body = key[len("rag:metrics:"):]
                    if body.startswith("counter:"):
                        name = body[len("counter:"):]
                        result["counters"][name] = int(client.get(key) or 0)
                    elif body.startswith("latency:"):
                        name = body[len("latency:"):]
                        samples = [
                            float(v) for v in client.lrange(key, 0, -1) if _is_number(v)
                        ]
                        if samples:
                            result["latencies"][name] = latency_stats(samples)
                if cursor == 0:
                    break
        except Exception:
            pass
        return result

    @staticmethod
    def _key(kind: str, name: str) -> str:
        return f"rag:metrics:{kind}:{name}"


def _is_number(value) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _percentile(sorted_values: list[float], q: float) -> float:
    """Nearest-rank 分位数（简单、确定，无插值）。"""
    if not sorted_values:
        return 0.0
    index = max(0, min(len(sorted_values) - 1, int(len(sorted_values) * q)))
    return sorted_values[index]


# 进程内单例：worker 与 API 各持一个实例，指向同一 Redis，指标自然聚合。
_metrics_store: MetricsStore | None = None


def get_metrics() -> MetricsStore:
    global _metrics_store
    if _metrics_store is None:
        _metrics_store = MetricsStore()
    return _metrics_store


def reset_metrics() -> None:
    """测试用：清掉进程内单例。"""
    global _metrics_store
    _metrics_store = None


# 延迟统计的确定性口径，供测试断言复用。
def latency_stats(samples: list[float]) -> dict:
    sorted_s = sorted(samples)
    return {
        "count": len(samples),
        "avg_ms": round(sum(samples) / len(samples), 1),
        "p50_ms": round(_percentile(sorted_s, 0.5), 1),
        "p95_ms": round(_percentile(sorted_s, 0.95), 1),
        "min_ms": round(min(samples), 1),
        "max_ms": round(max(samples), 1),
    }
