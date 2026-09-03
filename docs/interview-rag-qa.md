# 多模态个人学习知识库 RAG 系统 — 面试官视角 Q&A（RAG 深挖版）

> **作者口径：** 本版不是照着模板背的，题目来自真实面试检索——面试官对「个人资料库 RAG 项目」高频问什么、会在哪里追问、凭什么判断你做过真系统。答案全部结合本项目真实代码、真实实现、真实数字作答，无一编造；凡 MVP 没做的，诚实标注边界并给出上生产方案。
>
> **答题铁律（沿用）**：① 每答末尾落一个真实数字或取舍；② 没做的诚实说「MVP 没做，上生产我会……」；③ 先讲业务因果链，不先倒技术栈；④ 每答 3~6 句，可深挖处标「追问可深挖」；⑤ 代码引用给 `文件:行号` 便于核实。
>
> **架构变更说明（2026-08 单库迁移）**：文档级路由门控已删除、每文档一 Milvus Collection 已迁移为单共享 Collection `rag_all` + `document_id` 分区（对齐行业主流）。因此当前生产检索数字以「单库」为准；正文中标（迁移前）的数字是旧架构的历史实测，保留用于对比，不复现于当前代码。

---

## 简历 · 问题对照索引（面试官逐条念简历提问的入口）

> **用法：** 面试官大概率照着简历逐条问——下表是「简历写了什么 → 本版哪几题接得住 → 他会往哪深挖」的入口；找不到入口时用附录 C 代码索引兜底。以下题号为本版编号。

| 简历要点 | 本版对应题 | 面试官大概率追问 |
|---|---|---|
| 项目简介：学生复习场景 · 统一解析 PDF/Word/PPT/截图错题/手写笔记 · 可追溯答案 | Q1/Q2/Q3 | 为什么做 RAG 不做微调；多模态为什么难；可追溯怎么保证 |
| 多模态解析 + Agentic 检索：保留页码/OCR 置信度/表格结构 · 视觉模型入库前补充理解 · LangChain ReAct · 意图路由决定检索范围 · 证据不足多轮补检 | Q18-Q21、Q23-Q28、Q29/Q30 | 扫描 PDF 怎么解；图里逻辑关系；意图路由实现；死循环防法；证据够不够怎么判断 |
| 混合检索 + 查询改写 + 量化评估：BM25+向量→RRF→路由门控+relevance 重排 · 改写+双路召回 · 74 题评估集 · 忠诚度 0.865 | Q4-Q11、Q27、Q31-Q34 | RRF 为什么不加权；为什么不用模型 reranker；评估集怎么建；数字可复现吗 |
| 完整工程化 + 个性化学习库：Milvus/MySQL/Redis 分工 · 增量入库不重建整库 · 用户隔离 · JWT 鉴权 · 上传 AI 审核 · 用户画像 · 会话持久化 | Q35-Q42、Q35/Q36 | 增量怎么维护；上传审核怎么做；鉴权为什么升级 JWT；画像怎么进路由 |

---

## 一、开场与项目定位（面试官第一轮）

### Q1. 一句话介绍你这个项目（STAR + 因果链）？

**A：** 场景是**学生复习**：资料格式不统一（教材 PDF、Markdown 笔记、网页、截图错题、手写笔记），专业术语和公式不适合纯向量检索，且答案必须能回到原资料供复盘。任务是把这些异构资料统一解析、切分、索引，让问答返回可追溯出处的答案。我的做法是**多模态解析（PDF 三分类 + MinerU/OCR/视觉理解）→ 6 种分块 Profile → 单共享 Milvus Collection `rag_all`（document_id 分区，行业主流）→ BM25+向量 RRF 混合检索 → 可解释重排 + 每文档多样性上限 → Agentic RAG（意图路由 + ReAct 补检）→ 会话记忆 + 用户画像**。结果：74 题评估集在 6 文档受限语料上 Recall@1=0.9324、Recall@5=1.0000、MRR=0.9509；全库 28 份文档 Recall@1=0.8378、Recall@5=0.9054；关系型 30 题 all_docs@5=0.60；答案忠诚度 0.865（单库迁移前测）；335 个测试函数。**取舍：为「可追溯」牺牲了一点端到端流畅——每个答案都强制标来源编号，宁可让模型说「资料中没有」，不编。** 追问可深挖：为什么把检索和生成拆成两层各评各的。

### Q2. 为什么用 RAG 而不是微调 / 长上下文？什么时候该用哪个？

**A：** 选 RAG 的因果很直接：**资料每天都在增删，微调意味着每次改资料都要重训，长上下文则把每轮成本推高一个量级且不解决「知识组织」**。RAG 把「记忆」从模型参数搬到可更新的外部索引——改一份资料只重索引那一份（增量入库），成本是 embedding 一次向量化，不是训练。我这里是私有学习资料 + 必须可溯源，天然是 RAG 的主场。**取舍：RAG 换不来「风格/领域说话方式」，那个归微调；我的画像层用轻量 profile 调回答风格（direct/guiding/socratic），用规则做微调想干的事，[agent_rag.py:627-657](backend/app/rag/agent_rag.py#L627)。** 追问可深挖：什么场景我会反选微调（固定领域高频模式）、什么场景会选长上下文（单篇极长文档）。

### Q3. 系统整体架构？离线入库和在线问答怎么分工？

**A：** 两阶段。**离线**：上传进 Redis 队列 → worker 后台跑「解析 → 清洗 → 分块 → 向量化 → 入库」，状态机 + 错误码可观测，[worker.py:98-195](backend/app/tasks/worker.py#L98)；所有文档写入**单共享 Milvus Collection `rag_all`**（`document_id` 分区）+ 从该库文本重建的全库 BM25 索引，[hybrid_pipeline.py:39](backend/app/rag/hybrid_pipeline.py#L39)、[hybrid_pipeline.py:163](backend/app/rag/hybrid_pipeline.py#L163)。**在线**：`/chat/agent` 走「改写 → 意图路由 → ReAct 循环检索 → 证据融合 → 生成」，每轮落库 MySQL（会话/画像）。**取舍：异步队列把重 IO 的解析移出 HTTP 请求线程，上传秒回 task_id；代价是入库最终一致，前端要轮询任务状态。** 追问可深挖：为什么解析要异步而不是同步？Redis 挂了会怎样（见 Q37）。

---

## 二、检索链路设计（最高频 · 必考）

### Q4. 检索链路整体怎么设计？为什么纯向量不够，要混合检索？

**A：** 因果链是：**语义检索解决「意思相近」，但专业术语、公式、题号、人名是精确匹配，向量对它们天然不敏感**。比如「RFC 7231」可能匹配到「HTTP 规范」（语义近但字面缺），「反转单链表用哪三个指针」向量能懂、BM25 能锚定「指针」二字。所以我在单共享库内做 **BM25（bigram 分词 + BM25Plus）与向量（bge-small-zh-v1.5, dim512, COSINE）双路召回，RRF k=60 融合**，[hybrid_pipeline.py:455](backend/app/rag/hybrid_pipeline.py#L455)。**数字：74 题全库（28 份文档）纯向量 R@1=0.797、纯 BM25=0.811、RRF 混合=0.851——混合不是玄学，是两路各补对方的盲区。** 追问可深挖：BM25 为什么用 BM25Plus 不用 Okapi（小语料负分，[bm25_store.py:40-43](backend/app/rag/hybrid/bm25_store.py#L40)）。

### Q5. 为什么用单 Collection + document_id 分区？文档级路由为什么删了？

**A：** 现在不是一份资料一个 Collection——那是 MVP 初版，已被数据推翻。**初版每文档一 Collection** 带来跨库 rank 制 RRF 的「榜首平局」：各库 rank-1 分箱不可比，弱匹配库的榜首和强匹配库的 top-5 打架，43 题 R@1 只有 0.465。**修法一度是分数制重排 + 4 gate 文档路由**，但 74 题消融证明路由只救 1/74 题（q102、0.001 分硬币翻转、不省算力），于是删路由、迁到**单共享 Collection `rag_all` + `document_id` 分区**——这才是行业主流：一次全局向量检索、隔离靠 metadata filter、删除按 `document_id` expr 删分片，[hybrid_pipeline.py:39](backend/app/rag/hybrid_pipeline.py#L39)。**诚实分账：消融预测单库 R@1≈0.9459 没兑现——no_route 基线是每库 RRF（每篇榜首自动高分=per-doc rank masking），不是单库全局竞争；实测 6 文档受限 R@1=0.9324、R@5=1.0000，全库 28 份 R@1=0.8378、R@5=0.9054。** 新坑：单库全局 RRF 把 top-K 集中到最相似单篇，跨文档覆盖掉到关系型 all_docs@5=0.30，加**每文档多样性上限**（MAX_PER_DOC_DIVERSITY=2，MMR 式，[routes_retrieval.py:100](backend/app/api/routes_retrieval.py#L100)）拉回 0.60。**删除/驳回绝不对 `rag_all` drop_collection，一律走 `delete_document_chunks(document_id)`（expr 删除 + flush + BM25 重载，[hybrid_pipeline.py:371](backend/app/rag/hybrid_pipeline.py#L371)）——这是运维红线。** 追问可深挖：单库后 `_relevance_score` 重排是否已成负担（raw RRF 实测全库 R@1 0.865 > relevance 0.838，见 Q10）。

### Q6. Embedding 模型怎么选的？为什么 bge-small-zh-v1.5 / dim512？评估过吗？

**A：** 项目是**中文学习资料**，所以淘汰通用英文模型；bge 系列在中文检索 benchmark 领先，small 是**速度/效果/本机部署**的平衡点——个人知识库量级不需要大模型，`bge-large` 的收益换不来笔记本推理开销。dim512 是模型固有维度，和 Milvus AUTOINDEX+COSINE 配合。模型路径相对 `.env` 启动坑专门锚定到 PROJECT_ROOT，[model_config.py:18-31](backend/app/rag/model_config.py#L18)。**诚实：我没做过「换 embedding 模型」的 A/B 评测——这是一个缺口；但评估体系已就位（74 题检索集），上生产第一个 A/B 就换它。** 追问可深挖：为什么模型放本机不调 API（成本/隐私/离线）。

### Q7. BM25 中文分词怎么做？为什么 bigram 不用 jieba？

**A：** 中文没有空格，分词是 BM25 的第一道坎。**单字切分区分度太低（几乎每个文档都含「定理」里的「定」），完整 jieba 又引入额外依赖**，所以取折中：**中文按 bigram（二元组）切分，英文/数字/公式按词保留**。「拉格朗日中值定理」→ 拉格朗/格朗日/朗日中…关键词的 bigram 只在相关 chunk 出现，命中率显著提升，[bm25_store.py:89-115](backend/app/rag/hybrid/bm25_store.py#L89)。另外过滤掉「什么/如何/怎么」这类疑问词，避免只含疑问词的查询命中的全是无关块，[bm25_store.py:82-87](backend/app/rag/hybrid/bm25_store.py#L82)。**数字：纯 BM25 在 43 题上 R@1=0.814——一个轻量 bigram 方案撑起了精确匹配这条腿。** 追问可深挖：bigram 的边界（长句、停用词）和上生产会换成什么。

### Q8. BM25 和向量怎么融合？为什么 RRF 不是加权？k 为什么 60？

**A：** 因为 **BM25 分数和向量相似度不在同一尺度，直接加权相加没有物理意义，而且最优权重随 query 漂移**（有的问题吃字面、有的吃语义）。RRF 只用排名位置：`score = Σ 1/(k + rank)`，某块在两路都靠前就分高——这是**无参数、无校准**的融合，[fusion.py:11-48](backend/app/rag/hybrid/fusion.py#L11)。k=60 是 RRF 的通用惯例（控制「只在一路靠前」的块的增益），单库迁移后重扫 k=40/60/80/100，74 题全库 Recall@1 全部 0.8378、Recall@5 0.9054 持平——**k 在 40~100 完全不敏感**。**数字：混合 RRF（0.851）比纯向量（0.797）在 74 题全库 R@1 高 0.054。** 追问可深挖：RRF 什么时候不适用（跨文档、独立查询链，见 Q27）。

### Q9. 跨文档结果怎么融合排序？单库下重排 + 多样性上限怎么工作？

**A：** 单库后 **RRF 的 rank 分跨文档直接可比**——当初「每文档一 Collection」的分箱不可比问题从根上消失，4 gate 路由不再需要。现在流程：**BM25+向量 RRF 融合**（[hybrid_pipeline.py:455](backend/app/rag/hybrid_pipeline.py#L455)）→ 每块算 `_relevance_score` 可解释分（`0.65×向量 + 0.25×词项 + 0.10×排位`，[routes_retrieval.py:353](backend/app/api/routes_retrieval.py#L353)）→ 按相关性排序 + **每文档多样性上限**（每文档至多 2 条再取下一篇，防 top-K 被最相似单篇垄断，[routes_retrieval.py:503-516](backend/app/api/routes_retrieval.py#L503)）；范围收窄靠 `document_id in [...]` metadata filter 而非路由。**数字：加多样性上限后 6 文档受限 R@5 从 0.9459 提到 1.0000、关系型 all_docs@5 从 0.30 提到 0.60。** 追问可深挖：`_relevance_score` 的权重是迁移前的调优遗产——单库下 raw RRF 排序实测更高（全库 R@1 0.865 vs 0.838），见 Q10。

### Q10. 为什么不用模型 reranker？你的「重排」怎么证明有效？

**A：** 诚实说：**模型 reranker（Cross-Encoder）是 MVP 没做的，当前「重排」是确定性的 `_relevance_score` 排序 + 每文档多样性上限**，[routes_retrieval.py:353](backend/app/api/routes_retrieval.py#L353)。选它的原因一直是个人库量级小、对延迟敏感——Cross-Encoder 每 query×候选过一遍模型，成本高且本地部署重。**但单库迁移后要诚实分账：RRF 排名已跨文档可比，relevance 重排的价值存疑——raw RRF 序 + 多样性上限实测更高（全库 R@1 0.865/R@5 0.973，relevance 序 0.838/0.905），重排目前更像「旧架构遗产」**；删不删取决于 `EVIDENCE_WEAK_THRESHOLD` 分数量纲重校（见 Q5 追问）。真正的升级路径仍是标准范式：**bi-encoder 粗排 → Cross-Encoder 精排**。**数字：单库下「原始 RRF 融合」本身就有全库 R@1=0.851；先量化确定性方案的边界（单库已把平局问题从根上消灭），再上模型 reranker 才有明确增量。** 追问可深挖：什么信号出现时该上模型 reranker（P@3 明显低于 P@1、bad case 是语义级误排）。

### Q11. 查询改写 / 问题扩展 / 双路召回？为什么需要，收益是什么？

**A：** 三个都做，但**都设了开关，用评测决定开不开**。改写（口语化→检索友好，含指代消解「刚才那个定理」）默认开；双路召回（改写主路 + 原问题副路）默认开——**改写没变化时副路直接跳过，零开销**，[agent_rag.py:532-539](backend/app/rag/agent_rag.py#L532)；扩展（broad 问题拆子主题）**默认关**。为什么关：30 题口语化评测里，扩展把「go语言怎么学」这类题从单遍完全失效救回后，ReAct 自主补检已经能覆盖（8/9 闭环），**加扩展反而 7/9 且 mrr 0.91→0.76——扩展省了 26% 工具调用但以丢覆盖为代价，净收益为负，[data/eval/report.md](data/eval/report.md#L160)**。**数字：这是「先建评测、再让数据决定开关」的典型——3 个开关里 2 个开 1 个关，每个都有评测背书。** 追问可深挖：改写失败的宽容回退（[agent_rag.py:215-217](backend/app/rag/agent_rag.py#L215)）。

---

## 三、分块（面试官最爱挖的 trap）

### Q12. 分块策略怎么设计？为什么不是固定长度？

**A：** 核心权衡是面试官最想听的：**切大了信息被稀释、切小了上下文被割裂**。固定长度忽略语义边界——技术文档的标题层级会被一刀切成两半。所以做的是 **6 种 Profile 按资料结构选型**：technical（markdown 标题切分，章节重建让标题进 chunk 正文）、long_form（语义边界 0.55）、layout（版面保留，表格/公式/图片独立）、short_qa（固定 500/重叠 60，适合问答卡片）、high_value（父子块 + 可选 Contextual）、spreadsheet（Excel 行组），[chunking_profiles.py:26-74](backend/app/rag/chunking_profiles.py#L26)。auto 模式按文件类型 + QA 标记 + 版面占比自动选，[chunking_profiles.py:96-137](backend/app/rag/chunking_profiles.py#L96)。**数字：结构化切分升级前后 43 题 R@1 0.930→0.977（迁移前语料实测）——分块选型是检索质量的上限，这是量化证明。** 追问可深挖：为什么 QA 检测（Q/A 标记≥2）优先于文件扩展名。

### Q13. chunk 大小怎么定？40/1600/500/60 这些数字从哪来？

**A：** 不是拍脑袋，是**配合 LLM 上下文 + 标题结构 + 评测**三层定的：`min_chunk_size=40` 是防「空块/半行」噪音，`max_chunk_size=1600` 是「一段能装下一个完整知识点、又不超出单条证据的 token 预算」——对应 ReAct 单条证据正文截断 800 字、token 预算 4000，[agent_rag.py:23-27](backend/app/rag/agent_rag.py#L23)；short_qa 的 `500/重叠60` 是问答卡片的典型量级，重叠 12% 防边界割裂。**取舍：1600 字的上限意味着「远超上限的超长段落」会被语义切分继续切开，宁可多块不漏整段。** 追问可深挖：chunk 大小会不会和数据里实际块长分布校验过（诚实：没做块长直方图校准，上生产会做）。

### Q14. 表格 / 图片 / 公式这些结构化内容怎么处理？

**A：** 三类做法。**表格**：Excel 行组切分、块内重复表头保行列语义；PDF/Word 表格解析成结构化块后作为**屏障块**——绝不与正文混并、独立保留溯源，[chunking_profiles.py:257-260](backend/app/rag/chunking_profiles.py#L257)。**图片**：视觉模型出描述，图片块和「邻近正文上下文」绑定——查「讲 XX 那张图/那个公式」时，图片块自身文本召回差，绑前块尾部+后块头部各 150 字当上下文，[hybrid_pipeline.py:40-42](backend/app/rag/hybrid_pipeline.py#L40)、[hybrid_pipeline.py:594-620](backend/app/rag/hybrid_pipeline.py#L594)。**公式**：OCR/原生公式进 formula 块，同样绑定上下文。**数字：CONTEXT_WINDOW=150 是「够补语义又不过量稀释」的实测值。** 追问可深挖：屏障块会不会造成「答案一半在表格一半在正文」的检索空窗。

### Q15. 长文档跨页怎么办？（跨页语义合并 + 页码归属）

**A：** PDF 一页一个块，连续主题经常跨页，直接入库会把一个论点切两半。做法是**同标题段落内跨页语义合并**：连续文本块满足同一 heading_path + 同一 source_type + 同一页（PPT 按 slide）就合并成连续规范文本，表格/公式/图片仍当屏障打断，[chunking_profiles.py:140-205](backend/app/rag/chunking_profiles.py#L140)。合并时记录 `_page_segments`（文本偏移→页码），入库时把 chunk 归属到具体页，保证溯源页码精确，[hybrid_pipeline.py:548-591](backend/app/rag/hybrid_pipeline.py#L548)。**数字：合并折叠空白得到连续文本，跨页块能归到「每个片段各自的页」，而不是整块标一个页——溯源精度是个人知识库的刚需。** 追问可深挖：`_page_segments` 的结构（偏移元组）和谁消费它。

### Q16. HTML 为什么回退逐块分？一个被数据推翻的「更先进」方案

**A：** 这个最有意思，是**「看着更先进，实测更差」的真实翻车**。我最初给 HTML 也上了章节重建（标题+正文合并成大块），期望标题真正进 chunk 正文提升检索。**实测反馈：合并大块后检索效果反而变差**——HTML 的结构噪声让合并块变成「大而杂」，于是回退成**每块独立成 chunk**（标题仍进 search_text 作为检索上下文），[chunking_profiles.py:116-124](backend/app/rag/chunking_profiles.py#L116)。**取舍：Markdown 保留章节重建（干净层级），HTML 回退逐块（实测更优）——同一个策略不能无脑套所有格式，这是分块选型必须「一格式一测」的教训。** 追问可深挖：为什么 HTML 会失败而 Markdown 不会（标签噪声 vs 纯文本层级）。

### Q17. Contextual Retrieval 用了吗？为什么默认关？

**A：** 高价值 Profile 实现了 `contextual_retrieval=True`（对每块用 LLM 补一段「这块在讲什么」的上下文再 embedding，解决小块缺失全局语境），但**生产默认关**，[chunking_profiles.py:58-66](backend/app/rag/chunking_profiles.py#L58)。原因：**它给每个 chunk 多一次 LLM 调用，入库成本线性上涨，且在我这个量级（个人库 28 个文件）收益没到非开不可**。**取舍：作为「可选增强」留给高价值资料（如要长期用的错题本），常规教材笔记不开——成本敏感场景用「图片/公式上下文绑定」这种确定性方案代替 LLM 补上下文。** 追问可深挖：如果量级到千级文档，Contextual Retrieval 的性价比拐点在哪。

---

## 四、多模态解析（个人知识库最能聊的实）

### Q18. 支持哪些格式？最难的是哪个？

**A：** 覆盖 PDF、Markdown、HTML、Word、PPT、Excel、TXT、图片/手写。**最难的是 PDF 的三分类问题**：电子 PDF 直接提文本，扫描 PDF 是纯图片必须 OCR，混合 PDF（有文本层但残缺/乱码）最难——所以做了文档级分类（native/scanned/mixed）再分路处理。其次难的是**版面还原**：pdfplumber 按阅读顺序提取（表格/标题检测），失败回退 PyPDF；PPT 以**幻灯片为单元**（slide_number 边界阻止跨页合并，[chunking_profiles.py:208-217](backend/app/rag/chunking_profiles.py#L208)）。**数字：实测扫描样本 OCR 出 48 个块、上传 E2E 41 块——每个格式都要「解析 + 分块」双测试才敢上生产。** 追问可深挖：为什么解析器要「主备回退」（pdfplumber 失败回退 PyPDF，[chunking_profiles.py 相关清洗](backend/app/rag/cleaning.py#L119)）。

### Q19. 扫描 PDF 怎么处理？（PDF 三分类 + OCR + 视觉模型）

**A：** 扫描件是「图片上印着字」，第一层是**文档级三分类**（native/scanned/mixed）决定走文本提取还是 OCR。第二层：**扫描页用 OCR（PaddleOCR）出文字，同时视觉模型出整页理解**——视觉结果优先作为图片的 canonical 内容，OCR 作为证据和回退，公式结果过质量门控才入库。**取舍：不「全都上 VLM」——VLM 是大模型、慢且贵，只做「OCR 置信度低」的局部复核和「整页理解」，确定性任务交给专用 OCR；这是面试官最爱听的分工原则。** 追问可深挖：图片内容入库后和正文检索怎么打通（Q14 的上下文绑定）。

### Q20. 「图片里的逻辑关系」怎么解决？（面试压轴题）

**A：** 这是多模态 RAG 的真瓶颈：**OCR 只能告诉你图里有哪些字，不能告诉你「判断框指向哪条分支」**。我这个项目当前做的是「图转文」路线——视觉模型出描述 + OCR 出文本 + 上下文绑定，能答「图里有什么」，**但「图的逻辑关系」（流程图谁指向谁、拓扑图的边）是 MVP 缺口**。上生产方案是三层：**① 视觉元素抽取**（检测矩形/菱形/箭头）；**② 关系建图**（节点拓扑 + 连接语义）；**③ 把关系图纳入检索与推理**，让模型沿着连线走逻辑。**取舍：图转文成本低能上线，关系抽取准但重——先用图转文覆盖 80% 场景，关系建图只对流程图/拓扑图类资料按需开。** 追问可深挖：怎么判断一份图值得上关系抽取（图密度/类型）而不是一律 caption。

### Q21. 解析质量怎么保证？（清洗层 + 质量门控 + 图片上下文绑定）

**A：** 三条防线。**清洗层**：所有格式统一做字符归一化、断连字符修复、页眉页脚/页码/水印剥离（正则 + 频次启发），**清洗为空时回退原文不丢块**——坏数据比没数据更伤检索，[cleaning.py:119-142](backend/app/rag/cleaning.py#L119)。**质量门控**：公式/视觉理解结果要过置信度才入库，低置信 OCR 只当证据不当 canonical。**图片绑定**：图片/公式块绑定邻近正文上下文（CONTEXT_WINDOW=150），解决「只靠图片自身文本召回差」。**数字：结构化切分升级后 43 题 R@1 0.930→0.977（迁移前语料实测）的增量里，清洗 + 分块 + 上下文绑定是主要贡献——解析是 RAG 质量的「垃圾进垃圾出」源头。** 追问可深挖：页眉页脚剥离的误伤怎么防（频次启发只在「多页重复」时生效）。

---

## 五、Agentic RAG（项目差异化）

### Q22. 为什么从 workflow 升级到 agent？架构是什么？

**A：** 起因是评测暴露的缺口：**单遍检索对「跨文档关系型问题」拼不齐材料——30 题里 all_docs@5 只有 0.60（单库 + 多样性上限后；旧架构更低 0.53）**。这是 LightRAG 声称要解决、而 agent 能更便宜解决的：让模型**自主决定补不补检**。架构是「Router + Agent」两层：**意图路由**（structured output 决定检索哪个文档分区）在前，**ReAct 循环**（Thought→Action→Observation）在中，用经典 AgentExecutor 而非 LangGraph——**项目里只有 ReAct 一段是 agentic，解析/入库/评测/路由全原生**。**数字：ReAct 把 all_docs@3 从 0.40 拉到 0.83、证据级闭环 86.7%，联合闭环 0.967——这是「为什么要 agent」的量化答案，[data/eval/report.md](data/eval/report.md#L96-L104)。** 追问可深挖：为什么不用 LangGraph（单段 ReAct 不需要状态机，AgentExecutor 足够且可控）。

### Q23. 意图路由怎么实现？路由到哪些文档分区？为什么不用纯规则？

**A：** 检索前先做一次意图路由，让 agent 知道该搜哪些资料而不是无脑全库。**路由输入是可读的资料目录**（document_id + 文件名 + 主题 + 子章节，最多 50 份），不是晦涩的 `rag_*` 集合名，[agent_rag.py:110-114](backend/app/rag/agent_rag.py#L110)；输出 `RouterDecision`：`scope ∈ {auto, all, selected}` + `document_ids`（最多 20）+ `complex_query` 标记，rationale 上限 200 字符，[agent_rag.py:61-69](backend/app/rag/agent_rag.py#L61)。规则：**点名主题/文件 → selected 只搜那几份；泛泛复习/对比 → auto；要覆盖全部 → all**，用户画像科目拼进 prompt 让路由偏向当前学习方向，[agent_rag.py:115-120](backend/app/rag/agent_rag.py#L115)。**为什么不用纯规则：泛泛/对比问题的意图没法用关键词穷举，但路由是「软决策」——路由错了有单库全量检索 + ReAct 补检兜底，所以模型没调工具就回退 auto 全库，绝不崩链路，[agent_rag.py:137-139](backend/app/rag/agent_rag.py#L137)。** 追问可深挖：`complex_query` 标记怎么被消费（放宽补检预算，见 Q28）。

### Q24. 「证据够不够」怎么判断？什么时候补检 / 反问？

**A：** 两个正交信号。**证据不足**：top-k 最高分低于阈值，前置探针先小范围搜（PROBE_TOP_K=5），selected 无命中升级全库，[routes_retrieval.py:959-981](backend/app/api/routes_retrieval.py#L959)。**问题模糊**：LLM 判「问题太泛/无主题/多答案方向」也触发。**补检 vs 反问**：证据不足且能补 → ReAct 继续搜（最多 4 次）；补不上或问题本身模糊 → **澄清门控反问用户 1~2 个具体问题**。这里有个真实校准故事：原阈值 EVIDENCE_WEAK_THRESHOLD=0.4 对 dim512 的 embedding 形同虚设（10 题 best score 全 ≥0.66 永不触发），改成「证据不足 OR LLM 模糊判断」双触发才真正生效，[routes_retrieval.py:925-956](backend/app/api/routes_retrieval.py#L925)。**数字：门控 ON 后 10 题里真模糊/超纲/无主题 3 类全触发反问、正常题不误伤，忠诚度 +0.31（0.52→0.83）但覆盖 −0.4——宁可不答也不误导。** 追问可深挖：为什么纯检索阈值物理上区分不出「真模糊」（真模糊题改写后也能匹配到教程，语义距离天然近）。

### Q25. 怎么防止死循环？迭代上限怎么定？

**A：** ReAct 的「补检」本质是无限循环的温床，我用**三档预算**兜底：**MAX_ADDITIONAL_SEARCHES=4（1 次初始 + 最多 4 次补检 + 最终答案 = MAX_ITERATIONS=5）**，[agent_rag.py:18-21](backend/app/rag/agent_rag.py#L18)；单条观察证据 token 预算 4000、兜底作答 8000，**超 token 直接截断**；外加超限兜底——executor 被 max_iterations 截断时，不再把「Agent stopped due to max iterations」当答案返回，而是用已累积证据强制生成一次最终回答，[agent_rag.py:473-489](backend/app/rag/agent_rag.py#L473)。**取舍：5 轮上限在关系型评测里够用（30 题平均 3.8 次工具调用、证据闭环 26/30），且「宁可少补一次也不让用户等死循环」。** 追问可深挖：为什么阈值定 4 不是 8（评测平均 3.8 次、再放宽边际收益 < 风险）。

### Q26. 结构化输出怎么保证？（bind_tools + 宽容解析，DeepSeek 的坑）

**A：** 核心是 **bind_tools 而非 with_structured_output**：部分 OpenAI 兼容端点（含 DeepSeek thinking 模式）不支持 `response_format` 和强制 `tool_choice`，但支持自由 tool_calling——所以让模型「调用一个路由工具」而不是「强制输出 schema」，[agent_rag.py:93-146](backend/app/rag/agent_rag.py#L93)。再配**宽容解析范式**：改写/扩展剥代码围栏和引号、无有效行返回 []、异常一律回退默认，绝不破坏链路；模糊判断用正则搜 `"vague": true|false` 而非 JSON 解析，[agent_rag.py:353-358](backend/app/rag/agent_rag.py#L353)。**一个生产坑：LLM 在 rationale 里写代码花括号（interface { Run() }）被 ChatPromptTemplate 当模板变量解析直接抛错，30 题首次全量挂掉——修复是转义花括号 + rationale 截断 200 字符，崩溃题全恢复，证据闭环 23/30→26/30，[agent_rag.py:597-604](backend/app/rag/agent_rag.py#L597)。** 追问可深挖：宽容解析「失败静默回退」会不会掩盖真 bug（评测集兜底：解析失败有测试覆盖）。

### Q27. 多路召回怎么融合？_merge_queries 为什么不用 RRF？

**A：** 改写主路 + 原问题副路 + 扩展子问题路是**多条独立召回链，各自的分箱不同，RRF 只适用同一查询内的多路证据**——所以 `_merge_queries` 按 `(collection, chunk_index)` 取各路里的更高分再降序，某一路漏的关键词其它路能补回，[agent_rag.py:492-505](backend/app/rag/agent_rag.py#L492)。**取舍：跨查询取 max 是「最稳的保底」而不是「理论最优」——理论上各路归一化后 RRF 更平滑，但需要额外校准，个人库量级不值得。** 追问可深挖：副路何时跳过（改写未变化时，零开销，[agent_rag.py:536](backend/app/rag/agent_rag.py#L536)）。

### Q28. Agentic RAG 成本是普通 RAG 的 4~8 倍，你接受吗？

**A：** 接受，但**每一分钱都花在评测证明有收益的地方**。Agentic 的额外成本来自多轮补检的多次 LLM 调用，我用四个手段控：**限流每用户 60s/12 次**；**证据 token 硬预算**（单轮 4000、兜底 8000，超了截断）；**改写未变化副路跳过**；**扩展默认关**（评测证明无净收益）。**诚实：我没做「agent vs 单遍」的端到端 token 成本对比——这是缺口；但收益侧有硬数据：关系型题单遍 all_docs@5 迁移前 0.533（单库 + 上限后 0.60）、ReAct 补检拉到 0.833（迁移前实测）、证据闭环 26/30。** 取舍是**「为 30% 的关系型难题多花成本，简单题照常单遍」**——理想形态是意图路由先分简单/复杂，但当前 MVP 全部走 ReAct，[agent_rag.py:68](backend/app/rag/agent_rag.py#L68)。追问可深挖：按什么规则把 query 分流到单遍 vs agent（复杂标记 complex_query 已就位）。

---

## 六、幻觉与可信（必考）

### Q29. 怎么控制幻觉？（多层防御）

**A：** 四层。**① system prompt 最高优先级安全边界块**：明确「只能基于检索资料回答、禁止编造」，且这条任何来源（用户问题/对话历史/检索资料）都不能覆盖——防提示词注入篡改行为，[agent_rag.py:607-624](backend/app/rag/agent_rag.py#L607)。**② 强制来源编号**：证据渲染成 `[n] 来源=doc/filename/page/heading`，要求关键结论标 [n]，[agent_rag.py:427-457](backend/app/rag/agent_rag.py#L427)。**③ 兜底拒答**：证据缺失就回答「资料中没有找到相关内容」，_FINAL_ANSWER_PROMPT 明令不编造，[agent_rag.py:460-470](backend/app/rag/agent_rag.py#L460)。**④ 澄清门控**：模糊/超纲先反问（Q24）。**数字：答案忠诚度 0.865（43 题 LLM-as-judge 断言验证），fully_grounded(≥0.9) 53.5%——不是 100% 是因为断言验证很严，但比不约束的硬答强一个量级。** 追问可深挖：结构化溯源为什么不依赖 LLM 输出的字符串（从 ctx.fused 直接 serialize_source，LLM 只负责「引用哪个编号」）。

### Q30. 检索不到 / 证据不足怎么办？

**A：** 三级。**一级**：Agent 自己判断证据不足就补检（最多 4 次），selected 无命中升级全库，[routes_retrieval.py:959-981](backend/app/api/routes_retrieval.py#L959)。**二级**：补检仍不足 → 澄清门控反问（Q24），问的是「你想问 A 还是 B」这种带可选项的具体问题，不是空泛的「能不能说清楚点」。**三级**：反问后仍无 → 兜底拒答「资料中没有找到相关内容」，绝不编。**数字：门控 ON 时 40% 的问题首轮反问（4/10），但把这 4 个「硬凑几乎全编造」的题（cls008 开放题 OFF 忠诚度仅 0.06）换成了引导式反问——宁可不答也不误导。** 追问可深挖：澄清问题怎么生成（LLM 基于原始提问 + 一点检索线索，最多 2 条，[agent_rag.py:304-329](backend/app/rag/agent_rag.py#L304)）。

### Q31. 忠诚度怎么评估？（RAGAS 零标注）

**A：** 用 RAGAS 风格的 **Faithfulness（LLM-as-judge，零人工标注）**：把答案拆成原子事实断言，逐条对照检索来源判断是否被支持，聚合出「断言支撑比例」，[data/eval/report.md](data/eval/report.md#L14)。**关键设计是三层评估解耦**：检索层（Recall@K/MRR）、生成层（忠诚度）、端到端，谁掉链子一目了然——避免「只评回答顺不顺」掩盖检索层缺陷。**数字：43 题平均忠诚度 0.865、grounded(≥0.7) 79%；而且能逐题暴露薄弱点（q012 反转链表 0.4375、q004 二叉树 0.5 是最低分）。** 追问可深挖：LLM-as-judge 的偏置风险（位置/长度偏置）怎么控——同题多断言平均，且 judge 只看「断言是否被来源支持」不看「好不好听」。

---

## 七、评估体系（面试官判断你做过真系统）

### Q32. 检索质量怎么评估？指标和工具？

**A：** 三层评估体系，每一层独立指标：**检索层**用文档级评估集做 Recall@1/3/5、Precision@1/3、MRR，四路对比（纯向量 / 纯 BM25 / 原始 RRF / 生产链路）——同一评估集一改就能看出哪层贡献、哪层拖后腿，[data/eval/report.md](data/eval/report.md#L5-L14)。**生成层**用忠诚度（Q31）。**关系型层**用 doc_coverage/all_docs/mrr_any 专门测多文档拼接（Q34）。**工具是纯脚本 + JSONL 评估集，不进产品代码**——`scripts/eval_retrieval.py`、`eval_faithfulness.py`、`eval_relational.py`。**数字：43 题 → 74 题扩容是评估体系的自我进化——43 题区分度不足（接近天花板看不出好坏），74 题暴露了类型定义/语义相似文档的跨文档混淆。** 追问可深挖：为什么「扩题 + 单库化都会让人感觉系统变差」（0.977→0.932→全库 0.838）——扩题是评估变严，单库是把每库 RRF 的 per-doc rank masking 去掉后的更真实全局竞争，都不是系统退化。

### Q33. 具体数字？切片 / 重排 / 权重调优各带来什么？

**A：** 增益要分「迁移前架构」和「单库现状」两笔账，[data/eval/report.md](data/eval/report.md#L19-L84) 有迁移前全部出处。**迁移前实测（历史）**：① 切片升级（清洗层 + 标题重建 + 跨页合并）让 43 题 R@1 0.930→0.977；② 重排层把原始跨库 RRF 0.465（榜首平局）拉到 0.977；③ 权重调优把 74 题从旧权重 0.932 拉回 0.9595，敏感性扫描证明 0.65/0.25/0.10 局部最优。**单库迁移后（当前）**：路由门控删除、跨库 RRF 平局从根上消失；74 题全库生产链路 R@1=0.8378、R@5=0.9054、MRR=0.8601；6 文档受限（与消融同语料）R@1=0.9324、R@5=1.0000、MRR=0.9509。**诚实：迁移后全库 R@1 低于消融预测的 0.9459——预测基于每库 RRF（per-doc rank masking），单库全局竞争是不同量纲，详见 Q5。** 追问可深挖：为什么单库下 relevance 权重不再「局部最优」（raw RRF 序实测更高，见 Q10）。

### Q34. 多文档关系型问题怎么评估？LightRAG 要不要上？

**A：** 单独建了 30 题关系型评估集（`questions_relational.jsonl`，每题 2~3 个期望文档，覆盖 Go 语法桥接/跨语言对比/跨领域 join），指标是 doc_coverage / all_docs / mrr_any，分别测「入口找得到吗」「材料全吗」。**结论是「LightRAG 不需要」**：单遍 all_docs@5 迁移前 0.533、单库 + 多样性上限后提到 0.60——**单库化反而把关系型单遍覆盖提了一档**；ReAct 补检（迁移前端到端实测）拉到 0.833、单遍∪ReAct 联合闭环 0.967；剩下 4 题证据缺口里 3 题是「期望文档设得比实际严」（单文档已能答全，属正确行为）、1 题（rq005）是「可检索但 agent 停手太早」的规划层问题——**没有一个是检索层真漏**。**取舍：1/30 的边际收益撑不起图索引基础设施 + 实体抽取管线 + MySQL/Milvus 双写同步的运维成本；更便宜的方案是「问题点名文档/项目名时固定注入为检索关键词」直击 rq005 型规划缺口。** 追问可深挖：为什么 7 题时 57.1% 的 all_docs@5 是虚高（样本太小，扩容到 30 题才看到 53.3% 的真实水平）。

---

## 八、记忆与上下文

### Q35. 多轮对话上下文怎么管理？

**A：** 双保险。**滚动摘要压缩**：会话原文存 MySQL messages，超过 COMPRESS_THRESHOLD=14 条把旧消息折叠进滚动摘要（role=system），只保留最近 RECENT_WINDOW=8 条原文，[routes_retrieval.py:698-702](backend/app/api/routes_retrieval.py#L698)。**token 硬预算**：调 LLM 前把「摘要 + 最近原文」按 HISTORY_MAX_TOKENS=3000 封顶，超限从最旧丢起、**最新一条强制保留**——这是预防性守卫，防止粘贴超长文本撑爆上下文，[routes_retrieval.py:808-849](backend/app/api/routes_retrieval.py#L808)。**取舍：压缩是「丢细节保主干」的必然代价，所以只压缩到摘要、原文仍落库可查；3000 token 是「够多轮对话上下文又不过量」的经验值。** 追问可深挖：压缩阈值怎么定的、谁来判断该压缩（长度触发而非模型自判，[routes_retrieval.py:865-902](backend/app/api/routes_retrieval.py#L865)）。

### Q36. 长期记忆 / 用户画像怎么做？

**A：** 两级记忆。**用户画像**（subjects / weak_points / preferred_style）落 MySQL UserProfile，回答问题够长（≥200 字）时触发一次 LLM 画像判断，subject 一致性计数 ≥2 才落（STYLE_CONSENSUS=2，防单次误判漂移），[profile_evolution.py:91-123](backend/app/rag/profile_evolution.py#L91)；画像渲染进 system prompt 调回答风格（direct/guiding/socratic），[agent_rag.py:627-657](backend/app/rag/agent_rag.py#L627)。**用户记忆**（fact / preference / error_pattern）独立落库、可查看可删除。**诚实：没有「遗忘/衰减」机制**——记忆只增不改，这是 MVP 边界；上生产会加时间衰减和定期复核。**取舍：画像进化是「软失败」——LLM 判断失败就跳过，绝不因为画像逻辑拖垮问答。** 追问可深挖：画像怎么进路由（profile 科目拼进路由 prompt 让路由偏向当前学习科目，[agent_rag.py:115-120](backend/app/rag/agent_rag.py#L115)）。

---

## 九、生产与工程（个人项目里的工程感）

### Q37. 系统怎么保证可靠性？（降级 / 任务状态机 / 重试）

**A：** 四条。**降级**：MySQL 挂时带合法 token 的请求降级为 degraded 身份继续个人问答，管理操作 fail-closed 503；Milvus 连不上时目录重建为空不崩，[deps.py:29-74](backend/app/api/deps.py#L29)。**任务状态机**：Redis Hash 存 task 状态（pending/processing/done/failed…），结构化错误码 + 解析/OCR 失败率指标可观测，[task_store.py:1-127](backend/app/tasks/task_store.py#L1)。**重试**：LLM 调用超时 60s、重试 3 次；队列 at-least-once，worker 消费先查状态防重复标记。**幂等**：上传按 SHA256 content_hash 查重（approved>pending>rejected 优先级），URL 上传无 hash 跳过。**诚实缺口：没有任务级幂等键**——同一 task_id 双投可能重复处理，Milvus insert 无唯一约束兜底；上生产加消费端 SETNX 锁 + chunk_index 唯一约束。**数字：335 个测试函数、6 个 API 模块 50+ 端点——可靠性靠的不是吹，是测试网。** 追问可深挖：降级为什么不 fail-open（DB 挂时绝不因降级误升级权限，[deps.py 注释](backend/app/api/deps.py#L50)）。

### Q38. 权限边界怎么做？（真实鉴权 + 班级 + fail-closed）

**A：** 个人项目最容易忽视权限，我做了真实鉴权 + 权限分层。**鉴权**：JWT（HS256，7 天）+ bcrypt 密码哈希，无 header 一律 401，任何响应不外泄密码；无密码账号首登走引导式补设（scope=setup 短效 token，已设密 → 409 防抢先竞态），[security.py](backend/app/core/security.py)。**分层**：老师/学生角色，班级学习库——学生共享文档、上传经校验 agent 审核（prompt_injection 强信号强制驳回、fail-closed）、老师可审计放行/驳回/删除。**检索侧**：解析时资料归属 + 上传台账（unique class_id,user_id）决定谁能搜到，不是搜到再做过滤。**数字：审核 agent 对提示词注入 fail-closed——即使模型判 approved，注入强信号命中即驳回，可审计（review_payload 落库），[review_agent.py:21-101](backend/app/rag/review_agent.py#L21)。** 追问可深挖：为什么鉴权从轻量 X-User-Id 升级到真 JWT（裸 header 无法证明身份，伪造即越权）。

### Q39. 上传 AI 审核怎么实现？为什么用 LLM 审核不用纯规则？fail-closed 怎么保证？

**A：** 上传内容在入库前先过一道审核 agent（worker 的 REVIEW 阶段，在 INDEXING 之前），防垃圾/违规/恶意内容进检索。**实现是「单次结构化审核调用」不是多轮 ReAct**——够用且省 token，[review_agent.py:1-14](backend/app/rag/review_agent.py#L1)；判定结构 `ReviewDecision`：approved + reason(≤500) + category + confidence + **prompt_injection** 标记，[review_agent.py:21-34](backend/app/rag/review_agent.py#L21)。**为什么用 LLM 不用纯规则：提示词注入形态千变万化（忽略指令/扮演系统管理员/要求泄露 system prompt/嵌入隐藏指令），规则穷举不完，LLM 能看懂语义级隐藏指令**，[review_agent.py:71-83](backend/app/rag/review_agent.py#L71)。**fail-closed 在 worker 判定体现：`rejected = not approved OR prompt_injection`，注入强信号命中即驳回，文件标记 rejected（隐藏保留、不进检索），完整判定存 review_payload 供管理员审计放行，[worker.py:138-177](backend/app/tasks/worker.py#L138)。** 数字：驳回文件不删除可回溯、管理员放行优先（admin_owned 覆盖 agent 判定），审核调用失败也按放行记录低置信度——审核流程绝不阻塞入库、但每一条都有留痕，[worker.py:234-256](backend/app/tasks/worker.py#L234)。追问可深挖：为什么「模型含糊就放行」而不是一律拒收（误杀正常资料的成本 > 漏掉可人工复核的边界样本）。

### Q40. 为什么 Milvus 不是 FAISS？MySQL / Redis 各负责什么？

**A：** 选 Milvus 因为它是**分布式向量数据库**——FAISS 是纯内存库，多副本、持久化、动态扩容都要自己搭，个人项目不值；Milvus 开箱有 Collection 管理、AUTOINDEX、COSINE、HTTP 接口，单共享 Collection `rag_all` + `document_id` 分区天然映射文档注册表。**MySQL 管「元数据/身份/上传台账/会话记忆」（跨进程一致），Redis 管「任务队列 + 状态 + 限流 + 指标」，Milvus 管「向量」**——三者职责分明，这是把「JSON 注册表」升级到 MySQL 的真实动机（跨进程竞态）。**数字：向量化批处理 INDEX_BATCH_SIZE=200；BM25 索引重启时从 Milvus `rag_all` 文本重建（不双写，避免一致性问题），[hybrid_pipeline.py:163](backend/app/rag/hybrid_pipeline.py#L163)。** 追问可深挖：BM25 为什么不持久化（从 collection 重建是「一份数据源」的一致性强约束）。

### Q41. 增量入库不重建整库是怎么做到的？资料更新 / 删除怎么维护索引？

**A：** 关键机制是**单库内按 `document_id` 分区做增删**——所有 chunk 在同一个 `rag_all`，但每个都带 `document_id`。「新增/重传」由 worker 入库链路处理：**build 前先 `delete_document_chunks(document_id)` 幂等去重**（重入库换新 id、清旧 chunk），只动这一份、其它文档完全不动，[worker.py:318-329](backend/app/tasks/worker.py#L318)。**删除/驳回**走 `delete_document_chunks(document_id)`：Milvus `delete(expr='document_id=="..."')` + flush + BM25 全量重载，**绝不对 `rag_all` drop_collection**，[routes_documents.py:345](backend/app/api/routes_documents.py#L345)、[routes_org.py:288](backend/app/api/routes_org.py#L288)。**BM25 不双写持久化**——重启时从 `rag_all` 文本重建，向量和关键词永远来自同一份数据源，[hybrid_pipeline.py:163](backend/app/rag/hybrid_pipeline.py#L163)。**数字：28 个上传文件 = `rag_all` 内 1302 个 chunk，增量入库的成本只有「新文件自己的解析 + 分块 + 向量化」——这正是 Q2 说「改一份资料只重索引那一份」成立的原因。** 追问可深挖：任务级幂等缺口（同一 task_id 双投可能重复处理，见 Q37 可靠性）。

### Q42. 成本怎么控制？

**A：** 四个方向。**本地免费**：embedding 模型本机跑（bge-small-zh 免费，调 API 要钱），视觉模型只在入库阶段用、只对 OCR 置信度低的图复核。**限流**：每用户 /chat/agent 固定窗口 60s/12 次，[rate_limit.py](backend/app/api/rate_limit.py)。**token 预算**：历史 3000、单条证据 4000、兜底 8000，全链路 TokenUsageCallback 累加 input/output 可观测，[routes_retrieval.py:717-737](backend/app/api/routes_retrieval.py#L717)。**关掉无收益的开关**：查询扩展评测两轮无净收益 → 默认关；Contextual Retrieval 默认关。**数字：335 测试 + 限流 + 预算 + 开关，是「成本 = 每次调用 token × 调用次数」两条线都管。** 追问可深挖：token 硬预算在「上限」还是「目标」位置（是硬上限，超了丢最旧）。

---

## 十、面试官追问链（开放性压轴）

### Q43. 这个项目哪一层最限制上限？再给你一个月做什么？

**A：** 诚实排序：**① 检索层是当前上限**——有模型 reranker 没用、query 意图分流（简单题单遍/难题 agent）没做；**② 多模态的「图逻辑关系」**没解（Q20）；**③ 记忆无遗忘**。一个月优先级是：先上**模型 reranker A/B**（74 题评估集已就位，测了才知道值不值）→ **query 意图分流**降成本 → **图关系抽取**只对流程图/拓扑图类资料。**取舍：不做「换 embedding 微调」——先做收益可测的，每个都先建基线再动，避免「感觉变好」的自我安慰。** 追问可深挖：为什么 reranker 排第一（全库 P@3 0.514 明显低于 P@1 0.838，语义级误排有空间——多样性上限刻意牺牲了 P@3 换跨源覆盖，这正好是 reranker 的用武之地）。

### Q44. 最大的坑 / 翻车经历？

**A：** 三个真翻车，每个都有数字。**① 花括号注入**：LLM 在 rationale 里写代码花括号被 ChatPromptTemplate 当模板变量解析，30 题关系型评估首次全量直接失败——修复转义 + 截断后证据闭环 23/30→26/30，[agent_rag.py:597-604](backend/app/rag/agent_rag.py#L597)。**② 门控阈值形同虚设**：EVIDENCE_WEAK_THRESHOLD=0.4 对 dim512 分布不匹配（10 题 best score 全 ≥0.66 永不触发），改成「证据不足 OR LLM 模糊判断」才生效。**③ HTML 章节重建翻车**：「看着更先进」的合并大块实测更差，回退逐块（Q16）。**共同教训：先建评测、再让数据决定，而不是让「感觉更高级」决定。** 追问可深挖：评估如何暴露了这些 bug（全量评估 = 每题的崩溃/召回都能归因）。

### Q45. 做成生产级还缺什么？

**A：** 分四块诚实说。**检索**：模型 reranker、query 意图分流、embedding 模型 A/B。**工程**：任务级幂等键、Milvus 唯一约束、真多租户隔离（当前班级粒度）、流式输出（当前同步 JSON 无 SSE）。**多模态**：图逻辑关系抽取、音频转写与时间戳溯源。**信任**：矛盾证据检测（当前跨文档矛盾是都进上下文让模型自裁）、记忆衰减。**取舍：按「可测性」排序——每个缺口都先给一个量化验证方案，而不是空谈上生产。** 追问可深挖：为什么流式输出排这么后（个人库问答延迟可接受，SSE 是体验优化不是正确性问题）。

---

## 附录 A：面试官追问工具箱（每个模块他会怎么追问 + 你的弹药）

| 模块 | 面试官追问方向 | 你的弹药（本项目真实答案） |
|---|---|---|
| 分块 | chunk 大小/overlap 怎么定的？为什么不用固定长度？ | 6 Profile 按结构选型；40~1600 配合证据 token 预算；切片升级 R@1 0.930→0.977（迁移前） |
| 检索 | 纯向量够吗？BM25 中文怎么分词？ | 术语/公式需精确匹配；bigram 折中；混合 RRF 0.851 vs 纯向量 0.797（74 题全库） |
| 融合 | 为什么 RRF 不用加权？k 取多少？ | 分数不同尺度、权重随 query 漂移；k=60 扫描四档持平 |
| 重排 | 为什么不用 Cross-Encoder？ | 诚实：MVP 没用；单库已让 RRF 跨文档可比，relevance 重排是旧架构遗产（raw RRF 实测更高，全库 R@1 0.865 vs 0.838）；Cross-Encoder 才是真升级路径 |
| Agent | 为什么 LangGraph？死循环怎么防？结构化输出？ | 单段 ReAct 不需要状态机；5 轮上限 + token 预算 + 超限兜底；bind_tools 适配 DeepSeek |
| 意图路由 | 路由到哪些文档分区？为什么不用纯规则？ | RouterDecision scope=auto/all/selected + complex_query；bind_tools 适配 DeepSeek；路由错了有单库全量检索 + ReAct 补检兜底 |
| 幻觉 | 检索不到怎么办？怎么证明没编？ | 兜底拒答 + 澄清门控 + 来源编号；忠诚度 0.865 LLM-as-judge |
| 评估 | 怎么证明检索准？指标？ | Recall@K/MRR 四路对比 + 74 题 + 敏感性扫描 + 关系型 30 题 |
| 多模态 | 扫描 PDF 怎么解？图里的逻辑关系？ | PDF 三分类 + OCR + VLM；图转文已做、关系抽取是明确缺口 + 三层方案 |
| 成本 | agent 4~8 倍成本接受吗？ | 限流 + token 预算 + 无收益开关全关；关系型收益有硬数据 |
| 上传审核 | 内容安全怎么把关？提示词注入怎么拦？ | 入库前单次结构化审核；prompt_injection 强信号 fail-closed 强制驳回、可审计放行 |
| 增量维护 | 资料更新/删除怎么动索引？ | 单库按 document_id 分区：build 前 delete_document_chunks 幂等去重；删除/驳回走 delete_document_chunks 绝不 drop rag_all；BM25 从 rag_all 重建不双写 |

## 附录 B：关键数字速查表（面试可脱口而出）

| 领域 | 数字 | 含义 |
|---|---|---|
| 检索 | R@1=0.9324（6文档受限）/ 0.8378（全库 28 份） | 单库生产链路 top-1 正确文档命中率 |
| 检索 | MRR=0.9509（6文档受限）/ 0.8601（全库） | 首个正确命中的平均倒数排名 |
| 检索 | R@5=1.0000（6文档受限）/ 0.9054（全库） | 每文档多样性上限（MAX_PER_DOC=2）后的跨源覆盖 |
| 重排 | 0.465 → 0.977（迁移前） | 旧架构跨库 RRF 榜首平局的修复（已随单库从根上消失） |
| 切片 | 0.930 → 0.977（迁移前） | 结构化切分升级前后 R@1（43 题同批文档） |
| 权重 | 0.932 → 0.9595（迁移前） | 74 题下旧权重 0.55 → 新权重 0.65 的 R@1 |
| 生成 | 忠诚度 0.865（迁移前测） | RAGAS 式断言支撑比例（43 题零人工标注） |
| 关系型 | 单遍 all_docs@5=0.60（单库）；ReAct 0.833 / 联合 0.967（迁移前） | 30 题跨文档拼接 |
| 澄清 | 反问率 40%，忠诚度 +0.31 | 门控 ON vs OFF（10 题） |
| 路由 | scope=auto/all/selected + complex_query | 意图路由分档 + 复杂题放行多轮补检 |
| Agent | MAX_ITERATIONS=5（1+4 补检） | ReAct 循环上限 |
| 证据 | MAX_EVIDENCE_TOKENS=4000 / 8000 | ReAct 单轮 / 兜底作答证据 token 预算 |
| 记忆 | HISTORY_MAX_TOKENS=3000 | 历史上下文 token 硬预算（最新一条强制保留） |
| 记忆 | COMPRESS_THRESHOLD=14 / RECENT_WINDOW=8 | 对话压缩阈值 / 保留原文轮数 |
| 分块 | 6 个 Profile / CONTEXT_WINDOW=150 | 分块选型 / 图片公式上下文绑定窗口 |
| 解析 | PDF 三分类 / OCR 48 块 | 扫描件处理 / 实测 OCR 块数 |
| 成本 | 限流 60s/12 次 / 重试 3 次 / 超时 60s | LLM 稳健性参数 |
| 工程 | 335 测试函数 / 50+ 端点 / 28 上传文件 | 自动化与接口/数据规模 |
| 评估 | 74+30+10+9 题四套评估集 | 检索 / 关系型 / 澄清 / 口语化 |

## 附录 C：核心代码索引（面试被追问时直接指过去）

| 话题 | 代码位置 |
|---|---|
| ReAct 循环上限 / 证据预算 / 超限兜底 | [agent_rag.py:18-29](backend/app/rag/agent_rag.py#L18)、[agent_rag.py:473](backend/app/rag/agent_rag.py#L473) |
| 意图路由（bind_tools + rationale 截断） | [agent_rag.py:93-146](backend/app/rag/agent_rag.py#L93) |
| 改写 / 扩展 / 澄清 / 模糊判断（宽容回退范式） | [agent_rag.py:149-386](backend/app/rag/agent_rag.py#L149) |
| 多路召回融合（(collection,chunk) 取 max） | [agent_rag.py:492-505](backend/app/rag/agent_rag.py#L492) |
| 证据渲染三重约束（块数/去重/token） | [agent_rag.py:427-457](backend/app/rag/agent_rag.py#L427) |
| system prompt 安全边界块 + 画像注入 | [agent_rag.py:607-657](backend/app/rag/agent_rag.py#L607) |
| 花括号转义（生产坑修复） | [agent_rag.py:597-604](backend/app/rag/agent_rag.py#L597) |
| 单库融合 + 每文档多样性上限 | [routes_retrieval.py:408-536](backend/app/api/routes_retrieval.py#L408)、[routes_retrieval.py:100](backend/app/api/routes_retrieval.py#L100) |
| 单库删除/增量（绝不 drop rag_all） | [hybrid_pipeline.py:371](backend/app/rag/hybrid_pipeline.py#L371)、[worker.py:318-329](backend/app/tasks/worker.py#L318) |
| 澄清门控（证据不足 OR 模糊） | [routes_retrieval.py:925-956](backend/app/api/routes_retrieval.py#L925) |
| 证据探针 / 升级全库 | [routes_retrieval.py:959-981](backend/app/api/routes_retrieval.py#L959) |
| 对话压缩 + 历史 token 硬预算 | [routes_retrieval.py:698-706](backend/app/api/routes_retrieval.py#L698)、[routes_retrieval.py:808-849](backend/app/api/routes_retrieval.py#L808) |
| 四段 workflow（意图/ReAct/落库/画像） | [routes_retrieval.py:1010-1307](backend/app/api/routes_retrieval.py#L1010) |
| 混合检索（BM25+向量 RRF k=60） | [hybrid_pipeline.py:444-453](backend/app/rag/hybrid_pipeline.py#L444)、[fusion.py:11](backend/app/rag/hybrid/fusion.py#L11) |
| BM25 bigram + BM25Plus | [bm25_store.py:40-115](backend/app/rag/hybrid/bm25_store.py#L40) |
| 分块 6 Profile 选型 + HTML 特判 | [chunking_profiles.py:26-137](backend/app/rag/chunking_profiles.py#L26) |
| 跨页语义合并 + 页码归属 | [chunking_profiles.py:140-205](backend/app/rag/chunking_profiles.py#L140)、[hybrid_pipeline.py:548](backend/app/rag/hybrid_pipeline.py#L548) |
| 图片/公式上下文绑定 | [hybrid_pipeline.py:594-620](backend/app/rag/hybrid_pipeline.py#L594) |
| Embedding 模型相对路径锚定 | [model_config.py:18-31](backend/app/rag/model_config.py#L18) |
| 清洗层（页眉页脚/水印剥离） | [cleaning.py:119-142](backend/app/rag/cleaning.py#L119) |
| 画像进化（风格漂移防抖） | [profile_evolution.py:91-123](backend/app/rag/profile_evolution.py#L91) |
| 上传校验 agent（fail-closed + 可审计） | [review_agent.py:21-101](backend/app/rag/review_agent.py#L21) |
| 任务状态机 + 结构化错误码 | [task_store.py:1-127](backend/app/tasks/task_store.py#L1)、[worker.py:29-34](backend/app/tasks/worker.py#L29) |
| 降级身份 / fail-closed | [deps.py:29-74](backend/app/api/deps.py#L29) |
| 鉴权（bcrypt + JWT HS256） | [security.py](backend/app/core/security.py) |
| 评测报告（全部数字出处） | [data/eval/report.md](data/eval/report.md) |

> **面经补充说明：** 本版题目与追问方向来自 2026 年 RAG/Agent 岗位真实面试检索（牛客、CSDN、InfoQ、GitCode、nowcoder 面经等），核心趋势是——面试官不再满足于「会用」，会追问到 trade-off、评测、翻车经历；「检索链路设计 / 分块 / 混合检索 / 重排 / 幻觉治理 / 评估体系」是六大连问模块。以上答案对应的具体做法在本仓库均可核实。
