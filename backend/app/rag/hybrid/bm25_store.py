"""BM25 关键词索引。

负责精确关键词、公式、题号的召回。与向量检索互补：
向量管"意思相近"，BM25 管"字面相同"。

使用 rank-bm25 的 BM25Okapi。语料在构建时一次性传入 chunk 文本，
查询返回 (chunk_index, score) 或直接返回命中的文本。
"""

from typing import Any

try:
    from rank_bm25 import BM25Okapi, BM25Plus
except ImportError:  # pragma: no cover
    BM25Okapi = None
    BM25Plus = None


class BM25Store:
    """基于 rank-bm25 的关键词检索索引。"""

    def __init__(self):
        self._bm25: Any = None
        self._corpus: list[str] = []
        self._metadata: list[dict] = []

    def build(self, corpus: list[str], metadata: list[dict] | None = None):
        """用语料构建 BM25 索引。

        Args:
            corpus: 每个 chunk 的文本列表。
            metadata: 与 corpus 一一对应的元数据（chunk_id, source 等），
                      检索时原样返回，用于溯源。
        """
        self._corpus = [c or "" for c in corpus]
        self._metadata = metadata or [{} for _ in corpus]
        if BM25Plus is None:
            raise RuntimeError("rank-bm25 未安装，无法使用 BM25 检索")
        # rank-bm25 需要 token 化后的语料（list of list of tokens）。
        # 用 BM25Plus：比 BM25Okapi 更能避免小语料/稀有 token 的负分问题，
        # 个人知识库语料规模小，BM25Okapi 的负分会影响排序稳定性。
        tokenized = [self._tokenize(doc) for doc in self._corpus]
        self._bm25 = BM25Plus(tokenized)

    @property
    def size(self) -> int:
        return len(self._corpus)

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """检索，返回按分数倒序的结果列表。

        每个结果: {"index", "score", "text", "metadata"}
        """
        if self._bm25 is None or not self._corpus:
            return []
        # rank-bm25 需要分词；中文简单按字符切分，对专业词/公式命中足够
        tokens = [token for token in self._tokenize(query) if self._is_meaningful_query_token(token)]
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        # 返回按分数降序的 top_k
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        results = []
        for idx in order[:top_k]:
            # BM25Plus has a positive baseline for every document. Without an
            # overlap check, a query containing only question words still
            # returns unrelated chunks as lexical hits.
            document_tokens = set(self._tokenize(self._corpus[idx]))
            if not set(tokens).intersection(document_tokens):
                continue
            results.append(
                {
                    "index": idx,
                    "score": float(scores[idx]),
                    "text": self._corpus[idx],
                    "metadata": self._metadata[idx] or {},
                }
            )
        return results

    @staticmethod
    def _is_meaningful_query_token(token: str) -> bool:
        if not token:
            return False
        if any(word in token for word in ("什么", "如何", "怎么", "是否", "为何", "哪些", "能否")):
            return False
        return not any(char in token for char in "的是吗呢吧么")

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """中文 tokenizer：用 bigram（二元组）切分，兼顾精度与轻量。

        单字切分区分度太低（几乎每个文档都含"定理"里的"定/理"），
        而完整 jieba 分词又引入额外依赖。bigram 是折中方案：
        "拉格朗日中值定理" -> 拉格朗 格朗日 朗日中 日中值 中值定 值定理
        关键词的 bigram 只在相关 chunk 中出现，命中率显著提升。
        英文/数字/公式按词切分保留。
        """
        import re

        tokens: list[str] = []
        # 把中英文数字按连续段拆开
        for seg in re.findall(r"[一-鿿]+|[a-zA-Z0-9_.\-/^²³₀-₉]+|[+\-*/=()\[\]{}]", text):
            seg = seg.strip()
            if not seg:
                continue
            if re.fullmatch(r"[一-鿿]+", seg):
                # 中文段生成 bigram
                if len(seg) == 1:
                    tokens.append(seg)
                else:
                    tokens.extend(seg[i : i + 2] for i in range(len(seg) - 1))
            else:
                tokens.append(seg)
        return tokens
