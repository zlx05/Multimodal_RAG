"""P2.1 运行指标测试：MetricsStore 计数/延迟采样/快照 + TokenUsageCallback。

用内存 fake redis（含 pipeline），不连真实 Redis。
"""

from fnmatch import fnmatch
from types import SimpleNamespace

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from backend.app.api.routes_retrieval import TokenUsageCallback
from backend.app.rag.metrics import MetricsStore, latency_stats


class _FakePipeline:
    def __init__(self, data: dict):
        self.data = data
        self._ops: list[tuple] = []

    def lpush(self, key, value):
        self._ops.append(("lpush", key, value))
        return self

    def ltrim(self, key, start, stop):
        self._ops.append(("ltrim", key, start, stop))
        return self

    def execute(self):
        for op in self._ops:
            if op[0] == "lpush":
                self.data.setdefault(op[1], []).insert(0, op[2])
            else:  # ltrim
                start, stop = op[2], op[3]
                self.data[op[1]] = self.data[op[1]][start: stop + 1 if stop >= 0 else None]


class FakeRedis:
    """只实现 MetricsStore 用到的子集。"""

    def __init__(self):
        self.data: dict = {}

    def incrby(self, key, n=1):
        self.data[key] = int(self.data.get(key, 0)) + n
        return self.data[key]

    def get(self, key):
        return str(self.data[key]) if key in self.data else None

    def lpush(self, key, value):
        self.data.setdefault(key, []).insert(0, value)

    def ltrim(self, key, start, stop):
        if key in self.data:
            self.data[key] = self.data[key][start: stop + 1 if stop >= 0 else None]

    def lrange(self, key, start, stop):
        lst = self.data.get(key, [])
        return lst[start: stop + 1 if stop >= 0 else None]

    def scan(self, cursor=0, match=None, count=None):
        keys = [k for k in self.data if not match or fnmatch(k, match)]
        return 0, keys

    def ping(self):
        return True

    def pipeline(self):
        return _FakePipeline(self.data)


def _store(fake=None):
    return MetricsStore(client=fake or FakeRedis())


def test_counter_incr_and_snapshot():
    fake = FakeRedis()
    store = _store(fake)
    store.incr("docs_ingested")
    store.incr("docs_ingested")
    store.incr("tokens_input", 1200)
    snap = store.snapshot()
    assert snap["counters"] == {"docs_ingested": 2, "tokens_input": 1200}
    assert snap["latencies"] == {}


def test_latency_observe_and_snapshot_stats():
    store = _store()
    for ms in (100, 200, 300, 400, 500):
        store.observe("chat_total_ms", ms)
    snap = store.snapshot()
    lat = snap["latencies"]["chat_total_ms"]
    assert lat["count"] == 5
    assert lat["avg_ms"] == 300.0
    assert lat["min_ms"] == 100.0
    assert lat["max_ms"] == 500.0
    # Nearest-rank：len=5 → p50=int(2.5)=2 → 第 3 大排序值 300；p95=int(4.75)=4 → 500
    assert lat["p50_ms"] == 300.0
    assert lat["p95_ms"] == 500.0


def test_latency_window_trimmed_to_max_samples():
    store = _store()
    for i in range(1100):
        store.observe("index_ms", i)
    snap = store.snapshot()
    lat = snap["latencies"]["index_ms"]
    assert lat["count"] == 500
    assert lat["max_ms"] == 1099.0  # LPUSH 后最老的 600 条被 LTRIM 掉


def test_unreachable_redis_disables_and_noops():
    """client=None 的生产路径：连不上 Redis → 禁用并静默降级，不抛异常。"""
    store = MetricsStore(redis_url="redis://127.0.0.1:1/0")  # 端口 1 不可达
    store.incr("chat_queries")
    store.observe("parse_ms", 10)
    assert store.snapshot() == {"counters": {}, "latencies": {}}
    assert store._disabled is True  # 一次失败后禁用，避免每个请求重连


def test_latency_stats_pure_function():
    stats = latency_stats([10, 20, 30, 40])
    assert stats["count"] == 4
    assert stats["avg_ms"] == 25.0
    assert stats["p50_ms"] == 30.0
    assert stats["p95_ms"] == 40.0


def test_token_usage_callback_sums_usage():
    cb = TokenUsageCallback()
    result = LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(
                        content="a",
                        usage_metadata={"input_tokens": 5, "output_tokens": 7, "total_tokens": 12},
                    )
                ),
                ChatGeneration(
                    message=AIMessage(
                        content="b",
                        usage_metadata={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                    )
                ),
            ]
        ]
    )
    cb.on_llm_end(result)
    assert cb.input_tokens == 7
    assert cb.output_tokens == 10


def test_token_usage_callback_ignores_missing_usage():
    cb = TokenUsageCallback()
    result = LLMResult(generations=[[ChatGeneration(message=AIMessage(content="x"))]])
    cb.on_llm_end(result)  # 无 usage_metadata 不抛异常
    assert cb.input_tokens == 0
    assert cb.output_tokens == 0
