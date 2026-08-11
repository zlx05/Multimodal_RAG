# 检索评估集

评估 **纯向量 / 纯 BM25 / BM25+向量+RRF / 生产路径** 在真实库上的文档级召回质量。
运行评测脚本后这里会生成 `results.json`（最近一次全量结果）。

## 如何运行

```bash
conda activate rag11
# 从仓库根目录运行（脚本依赖仓库根的 .env 与模型相对路径）
python scripts/eval_retrieval.py                # 全量 43 题，四路对比
python scripts/eval_retrieval.py --limit 3      # 冒烟：只跑前 3 题
python scripts/eval_retrieval.py --variants vector,rrf
```

前提：Milvus 已启动、embedding 模型已下载、库内已有入库资料。输出对比表 + `data/eval/results.json`。

## questions.jsonl 格式

每行一个 JSON 对象（UTF-8），单相关文档标注：

```json
{"id": "q001", "question": "顺序表在第 i 个位置插入一个元素，平均需要移动多少个元素？", "document_id": "doc_3f570a771fd1", "filename": "data_structures.md"}
```

| 字段 | 说明 |
| --- | --- |
| `id` | 唯一字符串，用于报告与结果索引 |
| `question` | 问题文本，需真实可答（答案在 `document_id` 对应文档里） |
| `document_id` | **唯一**期望文档（每题只标一个） |
| `filename` | 仅供阅读，帮助确认文档（`document_id` 是不透明哈希） |

加题规则：
- 问题必须来自库里真实内容，不能用"假设库里有 X"的题。
- 每题**恰好一个** `document_id`（评估口径是文档级 Recall/MRR，多目标需改指标）。
- 校验：未知 document_id 会告警、重复 id 会报错、空 question 会报错。

## 指标定义（文档级）

对每题得到按分数降序的命中文档序列 `ranked_doc_ids`：

- **Recall@K(q)** = 1 若 `expected ∈ ranked_doc_ids[:K]`，否则 0。
- **MRR(q)** = 1 / (第一个 expected 命中的排名)，未出现则 0.0。
- 汇总 = 各变体在所有问题上的均值。

## 变体与合并说明

每道题对**全部** collection 检索再合并（衡量端到端选文档，不是单库召回）：

| 变体 | 单库检索 | 跨库合并 |
| --- | --- | --- |
| `vector` | `_vector_search`（cosine，跨库可比） | 按 cosine 降序 |
| `bm25` | `_bm25_search`（BM25Plus） | 按 BM25Plus 降序 |
| `rrf` | `search`（BM25+向量+RRF） | 同 `(collection, chunk_index)` 取 max RRF，再降序 |
| `production` | `_federated_search` 原样调用 | 路由门控 + relevance 重排 + top_k 截断 |

注意：**BM25Plus 分数按各自 collection 的 idf 归一**，跨库直接按分合并是近似做法；
RRF 合并则镜像 `_federated_search` 的候选排序，但**排除**路由门控与展示分重排（那是路由阶段的启发式，会让三路不可比）。

`production` 不是纯召回通道——它走完整生产链路（含路由门控、`_relevance_score`
重排、top_k 截断），是用户实际看到的排序。用它对比三路纯召回，能直接看到生产
重排环节把跨库 RRF 的"榜首平局"问题拉回多少。它不是纯通道，分数口径与前三行不可
直接比大小，但衡量的是同一份 ground truth。

chunk 身份是 `(collection_name, chunk_index)`，无全局 id；命中 → 文档通过
`HybridRAGPipeline._chunk_pool_by_index(index)` 的 `document_id` 字段映射。

## 已知噪声

- `doc_24fd41910512`（北邮大创立项申请书 PDF）第 9 页扫描件 OCR 质量差（手写/表格），
  只对确实在 PDF 文本里的问题标注它。
- `doc_d4c6ee949f71`（泰勒错题 png）只有 1 个 chunk（视觉模型描述文本），写题范围有限。

## 跨文档关系型评估（P3.1，LightRAG 缺口验证）

`questions_relational.jsonl` 是关系型评估集：每题 **2~3 个期望文档**，回答需要跨文档拼接
事实（Go 语法跨文档拼接/对比、数据结构与 Go 切片跨领域对比、大创申请书与图表 PPT 跨域拼接）。

```bash
python scripts/eval_relational.py                # 全量 7 题，生产链路单遍检索
python scripts/eval_relational.py --limit 3      # 冒烟
python scripts/eval_relational.py --top-k 12     # 提高每路召回数
```

文档 → collection 映射从 MySQL `documents` 表读取（JSON registry 只存 6 份旧文档）。
输出 `results_relational.json` + 对比表，`scripts/eval_report.py` 生成报告第六节。

多文档指标（`backend/app/rag/eval/metrics.py`）：

- **doc_coverage@K**：前 K 个（去重后）文档里找到的期望文档占比（部分召回）。
- **all_docs@K**：全部期望文档是否都在前 K（检索闭环，关系型问题可回答的必要条件）。
- **mrr_any**：第一个期望文档的倒数排名（有拼接入口的比例）。

## 后续

- 答案 groundedness 0/1/2 人工评分（复用本集，需 LLM 生成 + 人工判）。
- 若 RRF 明显优于两路纯检索，可用本集调参（per-collection top_k、RRF k、semantic floor）。
