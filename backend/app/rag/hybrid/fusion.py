"""混合检索结果融合（RRF，Reciprocal Rank Fusion）。

BM25 分数和向量相似度不在同一尺度，直接相加没有意义。
RRF 只使用排名位置：某个结果在两路中都靠前，融合分数就高。
公式：RRF_score(d) = sum(1 / (k + rank_i))，k 通常取 60。
"""

from collections import defaultdict


def reciprocal_rank_fusion(
    ranked_lists: list[list[dict]],
    k: int = 60,
) -> list[dict]:
    """把多路有序结果融合为一路。

    Args:
        ranked_lists: 每个元素是一个有序结果列表，元素为 dict，必须含 "index" 字段
                      （同一 chunk 在两路的 index 应一致，用它能去重合并）。
        k: RRF 常数，默认 60。

    Returns:
        融合后的有序结果列表（按分数降序），每个结果带 "score" 和来源 "origins"。
    """
    scores: dict[int, float] = defaultdict(float)
    origins: dict[int, list[str]] = defaultdict(list)
    signals: dict[int, dict[str, float]] = defaultdict(dict)

    for list_idx, ranked in enumerate(ranked_lists):
        for rank, item in enumerate(ranked, start=1):
            idx = item["index"]
            scores[idx] += 1.0 / (k + rank)
            origin = item.get("source", f"list_{list_idx}")
            origins[idx].append(origin)
            raw_score = item.get("score")
            if raw_score is not None:
                signals[idx][origin] = float(raw_score)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [
        {
            "index": idx,
            "score": float(score),
            "origins": origins[idx],
            "signals": signals[idx],
        }
        for idx, score in ranked
    ]
