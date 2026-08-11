# 学习文档：BM25 + 向量混合检索与溯源

> 目标读者：项目作者本人（要面试、要能讲清楚）。看完这份文档，你应该能：
> 1. 说清楚为什么只用向量检索不够、BM25 解决什么问题。
> 2. 复述混合检索的两路召回 + RRF 融合流程。
> 3. 说清楚 RRF 融合的公式、为什么用它、有什么坑。
> 4. 说清楚溯源字段怎么存、怎么用。
> 5. 回答面试官关于"检索错了 / 怎么评估召回"的追问。

---

## 1. 这一模块解决什么问题

### 1.1 向量检索的短板

向量检索（Embedding + 相似度）擅长"意思相近"，但不擅长"字面精确"：

| 查询类型 | 向量检索表现 | 为什么 |
| --- | --- | --- |
| "求极限有哪些方法" | ✅ 好 | 语义相似，能匹配到讲极限的段落 |
| "泰勒公式余项" | ⚠️ 一般 | 专业词向量可能不在附近 |
| "例题 3-2" | ❌ 差 | 题号是精确文本，语义上无相似度可言 |
| "洛必达法则" | ⚠️ 看情况 | 语料里若有则能命中 |

学生复习资料里大量存在**公式、题号、专业术语、英文缩写**——这些对字符级精确匹配敏感，向量检索对它们不稳定。

### 1.2 关键词检索的短板

关键词检索（BM25）擅长"字面相同"，但不懂语义。用户问"怎么求极限"，文档里写"洛必达法则 / 等价无穷小"，BM25 命中不了（字面没有"极限"）。

### 1.3 结论：两路召回互补

```
问题
  -> 查询清洗
  -> BM25 关键词召回（公式、题号、专业词）
  -> 向量召回（语义相近）
  -> RRF 融合 -> 去重 -> 重排
  -> 截取上下文 -> LLM 回答
```

这就是"混合检索"（Hybrid Retrieval）。

---

## 2. 核心概念

### 2.1 BM25 是什么

BM25 是一种**关键词相关性打分算法**，本质是改进版的 TF-IDF。对查询里的每个词，计算它和文档的相关程度：

- **词频（TF）**：词在文档里出现越多越相关。
- **逆文档频率（IDF）**：词在整个语料里越稀有越重要。比如"的"几乎每篇都有，权重低；"拉格朗日"很稀有，权重高。
- **文档长度归一化**：长文档里出现同一个词，不如短文档里相关性强。

BM25 输出一个分数，分数高说明文档和查询的关键词匹配度高。

### 2.2 为什么用 BM25 而不是 TF-IDF

BM25 对词频做了"饱和处理"：一个词在文档里出现 3 次和出现 10 次，分数不会线性翻倍。它更接近真实的相关性判断。而且 rank-bm25 这个库成熟轻量，中文只需简单分词即可用。

### 2.3 RRF 融合：怎么把两路结果合并

关键难点：**BM25 分数和向量相似度不在同一尺度**（BM25 可能是 0~15，余弦相似度是 0~1），直接相加没有意义。

RRF（Reciprocal Rank Fusion，倒数排名融合）的思路是：**不用原始分数，只看排名位置**。

```
RRF_score(d) = Σ 1 / (k + rank_i)

其中 rank_i 是文档 d 在第 i 路结果中的排名，k 是常数（通常取 60）。
```

例子：某 chunk 在 BM25 路排第 1，在向量路排第 2：
```
RRF = 1/(60+1) + 1/(60+2) = 0.0164 + 0.0161 = 0.0325
```

如果只在 BM25 路排第 1（向量路没召回）：
```
RRF = 1/(60+1) = 0.0164
```

这里的 `0.0164` 是 RRF 的**内部排名分**，不是百分比，也不是最终相关度。当前 API 将它保存在 `rrf_score`，前端展示的 `score` 是结合精确实体命中、关键词覆盖、向量相似度和最终排名计算的 0～1 匹配度。例如接口可能同时返回 `score: 0.94` 和 `rrf_score: 0.0164`，前者面向用户，后者用于调试融合过程。

**两路都靠前的结果，融合分明显更高**——这正好符合直觉：两路都命中的内容更可靠。

### 2.4 溯源字段：让答案"有出处"

检索出的 chunk 不只是文本，还携带来源元数据：

```python
{
    "text": "迪杰斯特拉算法 Dijkstra：求单源最短路径...",
    "document_id": "doc_ds",
    "filename": "数据结构完整复习笔记.md",
    "page": 3,                    # PDF 页码（图片没有则 None）
    "heading_path": "第三章 图 > 3.3 最短路径",
    "source_type": "markdown",    # pdf | image | markdown | text
    "image_path": "data/test/错题.png",  # 图片才有
    "origins": ["bm25", "vector"],  # 被哪几路召回
}
```

**为什么重要**：LLM 生成答案时，这些元数据随上下文一起传给模型，模型回答"迪杰斯特拉是贪心算法"时，前端就能展示"这段话来自《数据结构完整复习笔记.md》第三章"——用户点开即可复核。

---

## 3. 代码怎么读

### 3.1 BM25 存储：[hybrid/bm25_store.py](../backend/app/rag/hybrid/bm25_store.py)

```python
class BM25Store:
    def build(self, corpus, metadata):
        tokenized = [self._tokenize(doc) for doc in corpus]
        self._bm25 = BM25Plus(tokenized)   # BM25Plus 避免小语料负分

    def search(self, query, top_k=5):
        tokens = [
            token for token in self._tokenize(query)
            if self._is_meaningful_query_token(token)
        ]
        scores = self._bm25.get_scores(tokens)
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [
            {"index": idx, "score": scores[idx], ...}
            for idx in order
            if set(tokens) & set(self._tokenize(self._corpus[idx]))
        ][:top_k]
```

**关键点 1：为什么用 `BM25Plus` 而不是 `BM25Okapi`？**
rank-bm25 的 `BM25Okapi` 在小语料、稀有词场景下会产生**负分**，影响排序。个人知识库语料通常不大，`BM25Plus` 专门避免这个问题。

**关键点 2：中文怎么分词（tokenize）？**
```python
def _tokenize(text):
    # 中文段用 bigram（二元组）
    # "拉格朗日中值定理" -> 拉格朗 格朗日 朗日中 日中值 中值定 值定理
    # 英文/数字/公式按词切分
```

为什么不按单字切？单字切分区分度太低——"拉格朗日中值定理"拆成"拉/格/朗/日/中/值/定/理"，几乎每个文档都含"定""理"，BM25 分数就没了区分度。bigram 是"不引入 jieba 依赖"下的折中：关键词的 bigram 只在相关 chunk 中出现。

### 3.2 RRF 融合：[hybrid/fusion.py](../backend/app/rag/hybrid/fusion.py)

```python
def reciprocal_rank_fusion(ranked_lists, k=60):
    scores = defaultdict(float)
    for list_idx, ranked in enumerate(ranked_lists):
        for rank, item in enumerate(ranked, start=1):
            scores[item["index"]] += 1.0 / (k + rank)
            origins[item["index"]].append(item["source"])
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
```

**为什么用 `item["index"]` 去重？** 同一 chunk 在两路结果里 index 相同，靠它合并。不能用文本去重——同样的内容可能在不同文档/页码出现。

### 3.3 混合检索 Pipeline：[hybrid_pipeline.py](../backend/app/rag/hybrid_pipeline.py)

核心流程：

```python
def search(self, question, top_k=8, bm25_k=8, vector_k=8):
    bm25_results = self._bm25_search(question, top_k=bm25_k)     # 路1：关键词
    vector_results = self._vector_search(question, top_k=vector_k)  # 路2：语义
    return reciprocal_rank_fusion([bm25_results, vector_results])   # RRF 融合
```

- `_bm25_search`：调 BM25Store，返回带 index 和 chunk 元数据的结果。
- `_vector_search`：调 Milvus collection.search，返回向量相似度结果。
- 融合后，结果带 `origins` 字段，标记"来自哪几路"。

### 3.4 溯源字段怎么存进 Milvus

`HybridRAGPipeline._get_or_create_collection` 里，collection 字段比旧版多了来源元数据：

```python
fields = [
    FieldSchema("document_id", VARCHAR),   # 文档 ID
    FieldSchema("filename", VARCHAR),      # 原文件名
    FieldSchema("source_type", VARCHAR),   # pdf/image/markdown/text
    FieldSchema("page_number", INT64),     # PDF 页码
    FieldSchema("chunk_index", INT64),     # 分块序号（跨文档全局递增）
    FieldSchema("content", VARCHAR),       # 分块文本
    FieldSchema("heading_path", VARCHAR),  # 标题路径 "第三章 > 3.3"
    FieldSchema("image_path", VARCHAR),    # 原图路径
    FieldSchema("embedding", FLOAT_VECTOR),# 向量
]
```

分块时把 `block.heading_path`、`block.page_number`、`block.image_path` 都写进去。检索时通过 `output_fields` 带出来。

---

## 4. 实测结果（本项目验证）

在《数据结构完整复习笔记》上实测混合检索：

| 查询 | 命中的 chunk | 融合分 | 双路命中 |
| --- | --- | --- | --- |
| 迪杰斯特拉算法 | 迪杰斯特拉算法 Dijkstra | 0.0328 | ✅ bm25+vector |
| 图的深度优先遍历 | 深度优先搜索 DFS | 0.0328 | ✅ bm25+vector |
| 什么是不平衡二叉树 | AVL 树是左右子树高度差不超过1... | 0.0325 | ✅ bm25+vector |
| 堆排序 | 堆排序 + 排序算法对比表 | 0.0325 | ✅ bm25+vector |

**关键观察**：双路命中的结果分数(~0.032)显著高于单路命中的(~0.015)。这就是 RRF 的价值——"两路都命中"的信号被自动放大。

**小语料的坑**：语料只有 6 个 chunk 时，两路都召回几乎全部 chunk，RRF 分数趋同（都 ~0.031），排序区分度下降。这是 RRF 的固有特性，不是 bug。真实场景语料够大就有区分度。

---

## 5. 面试问答

### Q1：为什么不能只做向量检索？

**参考回答**：学生资料里大量是公式、题号、专业术语，这些对精确匹配敏感。向量检索擅长语义相似，但对"例题 3-2""泰勒公式余项"这类精确文本不稳定。所以加一路 BM25 做关键词召回，两路互补，召回更稳。

### Q2：BM25 分数和向量分数尺度不一样，怎么合并？

**参考回答**：不能直接相加。用 RRF 融合——只看排名位置，某 chunk 在两路都靠前就得到更高融合分。RRF 不需要分数归一化，工程上更简单稳定。

### Q3：RRF 的 k 为什么取 60？

**参考回答**：k 是常数，控制排名靠前的结果的权重。k 越大，排名差异对分数的影响越小（结果越平缓）；k 越小，排名越靠前越突出。论文和实践中 k=60 是常用默认值，也贴合我们的实测。

### Q4：怎么评估召回效果？（进阶）

**参考回答**：
- **Recall@K**：正确的 chunk 是否出现在前 K 个结果里。
- **MRR**：第一个正确结果的排名有多靠前。
- **nDCG**：多个相关结果的排序质量。
实践上会人工整理 30~50 个学生复习问题作为评测集，对比"纯向量 vs 混合检索"的指标。

### Q5：检索错了怎么办？（进阶）

**参考回答**：检索和生成拆开才能定位问题。如果"检索错了"（正确 chunk 没召回），调 BM25/向量召回策略、分块粒度、融合权重；如果"生成错了"（召回对了但模型没用对），调 Prompt。这就是为什么项目拆了 `/retrieval/search`（只检索）和 `/chat/ask`（检索+生成）两个接口。

### Q6：为什么保存 heading_path / page_number / image_path？

**参考回答**：溯源的核心。检索结果带来源元数据，LLM 生成时依据它标注"根据《数据结构笔记》第三章"，前端能展示原文片段和原图入口。没有这些，答案就"无法复核"，对复习场景是不可接受的。

### Q7：BM25 的中文分词怎么做？（追问）

**参考回答**：用 bigram（二元组）切分。因为单字切分区分度太低，完整 jieba 分词又引入额外依赖。bigram 在轻量和效果之间折中：关键词的二元组只在相关文档出现，BM25 分数有区分度。如果以后想提精度，可以换 jieba + 停用词。

---

## 6. 你该记住的"一句话总结"

> 混合检索 = BM25 关键词路（管公式、题号、专业词）+ 向量语义路（管口语化表达），两路结果用 RRF 融合（只看排名，不看分数），得到带溯源元数据的最终结果，交给 LLM 生成可复核的答案。

面试问"为什么用 BM25" → 答"专业名词、公式、题号需要精确匹配，向量检索对这些不稳定，两路互补"。问"怎么合并" → 答"RRF，只看排名，两路都命中的更可靠"。问"怎么评估" → 答"Recall@K / MRR / nDCG 评测集"。

---

## 7. 相关文件

| 文件 | 作用 |
| --- | --- |
| [hybrid/bm25_store.py](../backend/app/rag/hybrid/bm25_store.py) | BM25 索引 + bigram 中文分词 |
| [hybrid/fusion.py](../backend/app/rag/hybrid/fusion.py) | RRF 融合 |
| [hybrid_pipeline.py](../backend/app/rag/hybrid_pipeline.py) | 混合检索 Pipeline（Milvus + BM25 + LLM） |
| [blocks.py](../backend/app/rag/blocks.py) | DocumentBlock（含溯源字段来源） |
