"""答案 groundedness 纯函数指标：0/1/2 评分分布与汇总。

与 metrics.py 同约定：只依赖标准库，不 import Milvus / 模型 / app 内模块，
因此 backend/tests 可以在没有向量库和模型的情况下单测。

评分语义（人工逐题标注，参考回答是否被检索到的来源支持）：
- 0：不 grounded。答案核心内容在提供的来源里找不到（编造/答非所问），
    或虽然来自模型常识但来源列表无法支撑。
- 1：部分 grounded。答案关键点部分有来源支撑、部分没有；或引用来源编号
    与答案内容对不上。
- 2：充分 grounded。答案关键点都能在列出的来源中找到依据，来源编号标注合理。

交叉维度：expected_document_id 是否出现在 top sources（retrieval_hit），
用于区分「没召回到 → 答案才不 grounded」和「召回到了但答案没用对」。
"""

from typing import Iterable, Sequence

SCORE_LEVELS = (0, 1, 2)


def validate_score(score) -> int:
    """把标注值校验/归一成 0/1/2，非法值抛 ValueError。"""
    value = int(score)
    if value not in SCORE_LEVELS:
        raise ValueError(f"groundedness 评分必须为 0/1/2，收到: {value}")
    return value


def score_distribution(records: Sequence[dict]) -> dict[str, int]:
    """统计 0/1/2 各档数量。records 元素须含整型 score。"""
    distribution = {str(level): 0 for level in SCORE_LEVELS}
    for record in records:
        distribution[str(validate_score(record["score"]))] += 1
    return distribution


def aggregate_groundedness(records: Sequence[dict]) -> dict:
    """汇总 groundedness 指标，返回平均分 + 各占比 + 分档分布。

    records 为空时返回零值结构。
    """
    total = len(records)
    if total == 0:
        return {
            "questions": 0,
            "mean_score": 0.0,
            "fully_grounded": 0.0,
            "grounded": 0.0,
            "distribution": {"0": 0, "1": 0, "2": 0},
        }

    scores = [validate_score(record["score"]) for record in records]
    distribution = score_distribution(records)
    return {
        "questions": total,
        "mean_score": round(sum(scores) / total, 4),
        "fully_grounded": round(distribution["2"] / total, 4),
        "grounded": round((distribution["1"] + distribution["2"]) / total, 4),
        "distribution": distribution,
    }


def cross_with_retrieval(records: Sequence[dict]) -> dict:
    """groundedness × 检索命中的交叉表。

    按 retrieval_hit（expected 文档是否进入 top sources）分组，分别看
    groundedness 平均分与分布，回答「低分是召回问题还是生成问题」。
    records 元素须含 score（0/1/2）与 retrieval_hit（bool）。
    """
    buckets = {"hit": [], "miss": []}
    for record in records:
        key = "hit" if record["retrieval_hit"] else "miss"
        buckets[key].append(record)

    out: dict = {}
    for key, group in buckets.items():
        agg = aggregate_groundedness(group)
        out[key] = {
            "questions": agg["questions"],
            "mean_score": agg["mean_score"],
            "fully_grounded": agg["fully_grounded"],
            "distribution": agg["distribution"],
        }
    return out


def iter_scored(records: Iterable[dict]) -> list[dict]:
    """过滤出已标注（score 非 None）的 record，未标注的不计入指标。

    标注阶段 JSONL 里 score 可能为 null，聚合前用本函数剔除。
    """
    return [r for r in records if r.get("score") is not None]
