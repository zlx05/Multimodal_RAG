"""agent_rag 的单元测试：路由 schema、工具证据串、去重逻辑、查询改写、证据门控。

用 fake gateway / fake llm，不连 Milvus / LLM。AgentExecutor 本身需要真实 LLM，
这里只测不依赖 LLM 的部分（RouterDecision schema、build_tools 的证据合并、
rewrite_query 的宽容提取、routes_retrieval 的证据判定与前置门控）。
"""

from types import SimpleNamespace

from backend.app.rag.agent_rag import (
    AgentChatContext,
    RouterDecision,
    build_executor,
    build_tools,
    expand_query,
    generate_clarification_questions,
    judge_vague_question,
    rewrite_query,
    route_query,
    synthesize_final_answer,
    _history_text,
    _merge_dual,
    _merge_queries,
    _render_evidence,
    estimate_tokens,
)
from backend.app.api.routes_retrieval import (
    _evidence_sufficient,
    _probe_and_escalate,
    run_clarification_gate,
)


def _fake_gateway():
    def fake_chunk(index, doc="doc_1", content="chunk content 内容"):
        return {
            "document_id": doc,
            "filename": f"{doc}.md",
            "content": content,
            "page_number": 1,
            "heading_path": "标题",
            "metadata": {"topic_label": "主题"},
            "chunk_index": index,
        }

    def federated_search(question, collections, top_k, document_ids=None):
        return [
            {
                "collection": collections[0],
                "index": 0,
                "score": 0.9,
                "chunk": fake_chunk(0, content="答案块 alpha"),
            },
            {
                "collection": collections[0],
                "index": 1,
                "score": 0.7,
                "chunk": fake_chunk(1, content="相关块 beta"),
            },
        ], {"routing_strategy": "semantic_gate", "used_documents": []}

    return SimpleNamespace(
        resolve_collections=lambda scope, ids: ids or ["rag_coll_1"],
        federated_search=federated_search,
        serialize_source=lambda item: item,
        document_catalog=lambda: [
            {"document_id": "doc_1", "filename": "a.md", "topic_label": "主题"},
        ],
    )


def test_router_decision_schema_defaults():
    decision = RouterDecision()
    assert decision.scope == "auto"
    assert decision.document_ids == []
    assert decision.complex_query is False
    assert decision.rationale == ""


def test_router_decision_accepts_selected_scope():
    decision = RouterDecision(scope="selected", document_ids=["doc_1"], rationale="用户指定")
    assert decision.scope == "selected"
    assert decision.document_ids == ["doc_1"]


def test_route_query_feeds_subsections_into_prompt():
    """文档子章节标题（如「变量 > 比较」）必须进入路由 prompt，
    否则 router 只凭文件主题选文档，问题指向小节内容时易选错。"""
    llm = _ToolCallingLLM({"scope": "selected", "document_ids": ["doc_1"]})
    catalog = [
        {"document_id": "doc_1", "filename": "40_variable.html", "topic_label": "变量", "subsections": "声明；赋值；比较"},
        {"document_id": "doc_2", "filename": "10_datatype.html", "topic_label": "数据类型"},
    ]
    decision = route_query(llm, _fake_gateway(), "go语言怎么判断两个东西一样呀", catalog)
    assert decision.scope == "selected"
    assert decision.document_ids == ["doc_1"]
    prompt = llm.captured[0]
    assert "40_variable.html" in prompt
    assert "比较" in prompt
    # 带子章节的文档行拼出该字段；无 subsections 的文档行不应带空字段
    assert "40_variable.html | 主题=变量 | 子章节=声明；赋值；比较" in prompt
    assert "10_datatype.html | 主题=数据类型" in prompt
    assert "10_datatype.html | 主题=数据类型 | 子章节" not in prompt


def test_tools_merge_evidence_into_context():
    ctx = AgentChatContext()
    gateway = _fake_gateway()
    tools = {t.name: t for t in build_tools(ctx, gateway)}

    result = tools["search_library"].invoke("某个问题")
    assert "[1]" in result and "[2]" in result
    assert "答案块 alpha" in result
    assert len(ctx.fused) == 2
    assert (("rag_coll_1", 0)) in ctx.fused
    assert ctx.tool_calls[0]["tool"] == "search_library"


def test_tools_deduplicate_by_collection_index():
    ctx = AgentChatContext()
    gateway = _fake_gateway()
    tools = {t.name: t for t in build_tools(ctx, gateway)}

    tools["search_library"].invoke("第一次检索")
    tools["search_library"].invoke("第二次检索")  # 同样返回 (coll, 0)/(coll, 1)

    # 两次检索命中同一 (collection, index)，应去重而非叠加
    assert len(ctx.fused) == 2
    # 第二次分数 0.9 不应覆盖已有的 0.9（相等不覆盖），但分数更高应覆盖
    assert ctx.fused[("rag_coll_1", 0)]["score"] == 0.9


def _dual_gateway():
    """federated_search 按问题变化：改写路/原路返回不同命中，便于验证双路融合。"""

    def fake_chunk(index, doc="doc_1", content="chunk content 内容"):
        return {
            "document_id": doc,
            "filename": f"{doc}.md",
            "content": content,
            "page_number": 1,
            "heading_path": "标题",
            "metadata": {"topic_label": "主题"},
            "chunk_index": index,
        }

    def federated_search(question, collections, top_k, document_ids=None):
        calls.append(question)
        if question == "原问题":
            return [
                {"collection": collections[0], "index": 0, "score": 0.5, "chunk": fake_chunk(0, content="原路 0 块低分")},
                {"collection": collections[0], "index": 2, "score": 0.8, "chunk": fake_chunk(2, content="原路新块 2")},
            ], {"routing_strategy": "semantic_gate", "used_documents": []}
        return [
            {"collection": collections[0], "index": 0, "score": 0.9, "chunk": fake_chunk(0, content="答案块 alpha")},
            {"collection": collections[0], "index": 1, "score": 0.7, "chunk": fake_chunk(1, content="相关块 beta")},
        ], {"routing_strategy": "semantic_gate", "used_documents": []}

    calls = []
    return SimpleNamespace(
        resolve_collections=lambda scope, ids: ids or ["rag_coll_1"],
        federated_search=federated_search,
        serialize_source=lambda item: item,
        document_catalog=lambda: [],
    ), calls


def test_tools_dual_recall_merges_both_questions():
    """双路召回：副路（原问题）与主路（改写后）各查一次，按 (collection, index) 取高分融合。"""
    ctx = AgentChatContext()
    gateway, calls = _dual_gateway()
    tools = {t.name: t for t in build_tools(ctx, gateway, dual_question="原问题")}
    result = tools["search_library"].invoke("改写后问题")

    assert calls == ["改写后问题", "原问题"]  # 两路都被检索
    assert ctx.tool_calls[-1]["dual_recall"] is True
    # 主路 index 0 高分 0.9 > 副路 0.5 → 保留 0.9；副路独有 index 2 进入融合
    assert len(ctx.fused) == 3
    assert ctx.fused[("rag_coll_1", 0)]["score"] == 0.9
    assert ctx.fused[("rag_coll_1", 1)]["score"] == 0.7
    assert ctx.fused[("rag_coll_1", 2)]["score"] == 0.8
    # 结果串按分降序渲染，副路补回的块排在其应得名次
    assert result.index("[2]") < result.index("[3]")


def test_tools_dual_recall_skips_when_same_question():
    """改写未变化时副路等于主路，应跳过副路检索，零额外开销。"""
    ctx = AgentChatContext()
    gateway, calls = _dual_gateway()
    tools = {t.name: t for t in build_tools(ctx, gateway, dual_question="改写后问题")}
    tools["search_library"].invoke("改写后问题")

    assert calls == ["改写后问题"]  # 副路与主路相同 → 不重复检索
    assert ctx.tool_calls[-1]["dual_recall"] is False
    assert len(ctx.fused) == 2  # 只有主路结果


def test_merge_dual_takes_max_score_per_chunk():
    """_merge_dual 纯函数：同块取两路高分，异块都保留，按分降序。"""
    primary = [
        {"collection": "c", "index": 0, "score": 0.9},
        {"collection": "c", "index": 1, "score": 0.7},
    ]
    secondary = [
        {"collection": "c", "index": 0, "score": 0.5},  # 同一块，分更低 → 丢弃
        {"collection": "c", "index": 2, "score": 0.8},  # 副路独有块 → 保留
    ]
    merged = _merge_dual(primary, secondary)
    assert len(merged) == 3
    by_index = {int(item["index"]): item["score"] for item in merged}
    assert by_index[0] == 0.9
    assert by_index[1] == 0.7
    assert by_index[2] == 0.8
    # 融合后按分降序
    assert [item["score"] for item in merged] == [0.9, 0.8, 0.7]


def test_render_evidence_empty_context():
    ctx = AgentChatContext()
    assert "没有检索到可用证据" in _render_evidence(ctx)


def test_synthesize_final_answer_uses_accumulated_evidence():
    ctx = AgentChatContext()
    gateway = _fake_gateway()
    tools = {t.name: t for t in build_tools(ctx, gateway)}
    tools["search_library"].invoke("问题")
    llm = _CaptureLLM("可比较类型有：布尔、数字、字符串。")
    answer = synthesize_final_answer(llm, ctx, "问题")
    assert answer == "可比较类型有：布尔、数字、字符串。"
    # 已检索的证据必须进入兜底 prompt
    assert "答案块 alpha" in llm.captured[0]


def test_synthesize_final_answer_empty_evidence_no_crash():
    ctx = AgentChatContext()
    llm = _CaptureLLM("资料中没有找到相关内容。")
    assert synthesize_final_answer(llm, ctx, "问题") == "资料中没有找到相关内容。"


def test_synthesize_final_answer_llm_failure_returns_empty():
    ctx = AgentChatContext()
    assert synthesize_final_answer(_BoomLLM(), ctx, "问题") == ""


def _fused_item(collection, index, score, content, **overrides):
    chunk = {
        "document_id": f"doc_{collection}",
        "filename": f"doc_{collection}.md",
        "content": content,
        "page_number": 1,
        "heading_path": "标题",
        "metadata": {},
    }
    chunk.update(overrides)
    return {"collection": collection, "index": index, "score": score, "chunk": chunk}


def test_render_evidence_dedups_near_duplicate_parent_child():
    """parent-child 同时命中：子块正文是父块子串且长度占比≥0.9 → 只渲染高分父块。"""
    parent_text = "go变量声明" * 40  # 320 字符
    child_text = "go变量声明" * 36  # 288 字符，是父块子串，占比 0.9
    ctx = AgentChatContext()
    ctx.fused[("coll", 0)] = _fused_item("coll", 0, 0.9, parent_text)
    ctx.fused[("coll", 1)] = _fused_item("coll", 1, 0.8, child_text)

    rendered = _render_evidence(ctx)
    assert "[1]" in rendered
    assert "[2]" not in rendered  # 近重复子块被丢弃，不再重复占用上下文


def test_render_evidence_keeps_distinct_low_overlap_chunks():
    """两块内容互不包含（低重叠）→ 都保留并按分降序渲染。"""
    ctx = AgentChatContext()
    ctx.fused[("coll", 0)] = _fused_item("coll", 0, 0.9, "alpha" * 40)
    ctx.fused[("coll", 1)] = _fused_item("coll", 1, 0.7, "beta" * 40)

    rendered = _render_evidence(ctx)
    assert "[1]" in rendered and "[2]" in rendered
    assert "alpha" * 40 in rendered and "beta" * 40 in rendered


def test_render_evidence_respects_total_token_budget():
    """总 token 预算：超出时截断，优先保留更高分块；至少保留最高分一块。

    中文 300 字 ≈ 300 token（cl100k 实测 1 字≈1 token），max_tokens=400 下
    第一块（≈300 token）进入，第二块把累计推到 ≈600 token 超预算 → 截断。
    """
    ctx = AgentChatContext()
    ctx.fused[("coll", 0)] = _fused_item("coll", 0, 0.9, "高" * 300)
    ctx.fused[("coll", 1)] = _fused_item("coll", 1, 0.8, "中" * 300)
    ctx.fused[("coll", 2)] = _fused_item("coll", 2, 0.7, "低" * 300)

    rendered = _render_evidence(ctx, limit=10, max_tokens=400)
    assert "高" * 300 in rendered   # 最高分块优先进入
    assert "[2]" not in rendered    # 第二块把累计推到 ~600 token > 400 → 截断
    # token 预算是硬上限：正文 token 总和 ≤ 预算（编号/来源行不计入，因此留松弛）
    body_lines = [line for line in rendered.splitlines() if line and not line.startswith("[")]
    body_tokens = sum(estimate_tokens(line) for line in body_lines)
    assert body_tokens <= 400


def test_estimate_tokens_empty_and_small():
    assert estimate_tokens("") == 0
    assert estimate_tokens("hello") > 0


def test_estimate_tokens_chinese_approx_one_per_char():
    # 中文 1 字 ≈ 1 token（宽松区间避免绑定 tiktoken 版本差异）
    t = estimate_tokens("问" * 100)
    assert 80 <= t <= 120


def test_estimate_tokens_mixed_less_than_chars():
    # 英文为主的文本 token 数显著小于字符数（约 4 字/token）
    text = "the quick brown fox jumps over the lazy dog " * 50
    assert estimate_tokens(text) < len(text) // 2


def test_search_documents_uses_selected_ids():
    ctx = AgentChatContext()
    gateway = _fake_gateway()
    tools = {t.name: t for t in build_tools(ctx, gateway)}

    result = tools["search_documents"].invoke(
        {"question": "问题", "document_ids": ["doc_1", "doc_2"]}
    )
    assert "答案块 alpha" in result
    assert ctx.tool_calls[0]["tool"] == "search_documents"
    assert ctx.tool_calls[0]["document_ids"] == ["doc_1", "doc_2"]


class _CaptureLLM:
    """记录收到的 prompt，按配置返回改写结果。"""

    def __init__(self, result: str):
        self.result = result
        self.captured = []

    def invoke(self, prompt):
        self.captured.append(prompt)
        return SimpleNamespace(content=self.result)


class _BoomLLM:
    def invoke(self, prompt):
        raise RuntimeError("llm down")


class _ToolCallingLLM:
    """模拟支持 bind_tools 的模型：route_query 通过 tool_calls 拿路由决策。"""

    def __init__(self, args: dict):
        self.args = args
        self.captured = []

    def bind_tools(self, schema):
        return self

    def invoke(self, prompt):
        self.captured.append(str(prompt))
        return SimpleNamespace(tool_calls=[{"args": dict(self.args)}])


def test_rewrite_query_normal_rewrite():
    llm = _CaptureLLM("极限的定义是什么")
    assert rewrite_query(llm, "帮我看看极限的定义是啥") == "极限的定义是什么"
    assert "帮我看看" in llm.captured[0]


def test_rewrite_query_falls_back_on_empty():
    assert rewrite_query(_CaptureLLM("   "), "原始问题") == "原始问题"


def test_rewrite_query_falls_back_on_exception():
    assert rewrite_query(_BoomLLM(), "原始问题") == "原始问题"


def test_rewrite_query_strips_fences_and_quotes():
    for wrapped in ("```\n改写后问题\n```", '"改写后问题"', "「改写后问题」"):
        assert rewrite_query(_CaptureLLM(wrapped), "原始") == "改写后问题"


def test_rewrite_query_rejects_overlong_result():
    # 改写结果异常膨胀（>3 倍原长）视为不可信，回退原问题
    assert rewrite_query(_CaptureLLM("x" * 100), "短问题") == "短问题"


def test_rewrite_query_unchanged_when_same():
    assert rewrite_query(_CaptureLLM("原问题"), "原问题") == "原问题"


def test_evidence_sufficient_empty_is_no_evidence():
    assert _evidence_sufficient([]) == (False, "no_evidence")


def test_evidence_sufficient_weak_below_threshold():
    fused = [{"score": 0.3}, {"score": 0.2}]
    assert _evidence_sufficient(fused) == (False, "weak_evidence")


def test_evidence_sufficient_ok_above_threshold():
    fused = [{"score": 0.6}, {"score": 0.2}]
    assert _evidence_sufficient(fused) == (True, "sufficient")


def _selected_decision():
    return RouterDecision(scope="selected", document_ids=["doc_1"], rationale="路由")


def test_probe_escalates_when_selected_empty(monkeypatch):
    from backend.app.api import routes_retrieval as rr

    monkeypatch.setattr(rr, "_resolve_collections", lambda req: ["rag_coll_1"])
    monkeypatch.setattr(rr, "_federated_search", lambda q, c, k, document_ids=None: ([], {"routing_strategy": "none"}))
    req = SimpleNamespace(scope="auto")
    decision, escalated = _probe_and_escalate(req, _selected_decision(), "问题")
    assert escalated is True
    assert decision.scope == "all"
    assert decision.document_ids == []


def test_probe_keeps_decision_when_selected_has_hits(monkeypatch):
    from backend.app.api import routes_retrieval as rr

    monkeypatch.setattr(rr, "_resolve_collections", lambda req: ["rag_coll_1"])
    monkeypatch.setattr(rr, "_federated_search", lambda q, c, k, document_ids=None: ([{"score": 0.9}], {"routing_strategy": "lexical_gate"}))
    decision, escalated = _probe_and_escalate(SimpleNamespace(scope="auto"), _selected_decision(), "问题")
    assert escalated is False
    assert decision.scope == "selected"


def test_probe_does_not_escalate_user_locked_selected(monkeypatch):
    from backend.app.api import routes_retrieval as rr

    monkeypatch.setattr(rr, "_resolve_collections", lambda req: ["rag_coll_1"])
    monkeypatch.setattr(rr, "_federated_search", lambda q, c, k, document_ids=None: ([], {"routing_strategy": "none"}))
    req = SimpleNamespace(scope="selected")  # 用户显式锁定范围
    decision, escalated = _probe_and_escalate(req, _selected_decision(), "问题")
    assert escalated is False
    assert decision.scope == "selected"


def test_probe_exception_keeps_original_decision(monkeypatch):
    from backend.app.api import routes_retrieval as rr

    def boom(*args, **kwargs):
        raise RuntimeError("milvus down")

    monkeypatch.setattr(rr, "_resolve_collections", boom)
    decision, escalated = _probe_and_escalate(SimpleNamespace(scope="auto"), _selected_decision(), "问题")
    assert escalated is False
    assert decision.scope == "selected"


# ---------------------------------------------------------------------------
# Phase 4：改写上下文感知 + 问题扩展
# ---------------------------------------------------------------------------


def test_rewrite_query_includes_history_context():
    """带 chat_history 时，最近对话必须进改写 prompt（指代消解的依据）。"""
    history = [
        SimpleNamespace(type="human", content="go语言的数据类型是什么"),
        SimpleNamespace(type="ai", content="Go 语言的数据类型有整数、浮点、字符串等。"),
    ]
    llm = _CaptureLLM("go语言的变量声明")
    result = rewrite_query(llm, "它的变量声明", history)
    assert result == "go语言的变量声明"
    prompt = llm.captured[0]
    assert "go语言的数据类型是什么" in prompt
    assert "它的变量声明" in prompt


def test_rewrite_query_without_history_uses_placeholder():
    """不传历史时上下文块写（无），且改写链路行为不变。"""
    llm = _CaptureLLM("变量声明的方式")
    assert rewrite_query(llm, "变量声明的方式") == "变量声明的方式"
    assert "（无）" in llm.captured[0]


def test_history_text_filters_system_and_truncates_answer():
    """system 滚动摘要不参与改写上下文；助手长回答截断。"""
    long_answer = SimpleNamespace(type="ai", content="答" * 500)
    history = [
        SimpleNamespace(type="system", content="压缩摘要，不应出现"),
        SimpleNamespace(type="human", content="问题一"),
        long_answer,
    ]
    text = _history_text(history)
    assert "压缩摘要" not in text
    assert "问题一" in text
    assert len(text) < 500  # 长助手回答被截断


def test_expand_query_generates_subquestions():
    llm = _CaptureLLM("go语言的数据类型\ngo语言的输入输出\ngo语言的基本定义")
    assert expand_query(llm, "go语言怎么学") == [
        "go语言的数据类型",
        "go语言的输入输出",
        "go语言的基本定义",
    ]


def test_expand_query_none_when_specific():
    assert expand_query(_CaptureLLM("无"), "切片的容量是多少") == []


def test_expand_query_falls_back_on_exception():
    assert expand_query(_BoomLLM(), "go语言怎么学") == []


def test_expand_query_strips_prefixes_and_dedupes():
    llm = _CaptureLLM("1. go语言的数据类型\n2. go语言的数据类型\n- go语言的输入输出")
    assert expand_query(llm, "go语言怎么学") == ["go语言的数据类型", "go语言的输入输出"]


def test_merge_queries_n_way():
    """N 路融合：同块取多路高分，异块都保留，按分降序。"""
    a = [
        {"collection": "c", "index": 0, "score": 0.9},
        {"collection": "c", "index": 1, "score": 0.7},
    ]
    b = [
        {"collection": "c", "index": 0, "score": 0.5},  # 同块低分 → 丢弃
        {"collection": "c", "index": 2, "score": 0.8},
    ]
    c_ = [
        {"collection": "c", "index": 3, "score": 0.6},  # 第三路独有块
    ]
    merged = _merge_queries(a, b, c_)
    by_index = {int(item["index"]): item["score"] for item in merged}
    assert by_index == {0: 0.9, 1: 0.7, 2: 0.8, 3: 0.6}
    assert [item["score"] for item in merged] == [0.9, 0.8, 0.7, 0.6]
    # 薄封装 _merge_dual 与 _merge_queries 等价
    assert {int(item["index"]) for item in _merge_dual(a, b)} == {0, 1, 2}


def _expansion_gateway():
    """federated_search 按问题变化返回不同命中，验证扩展路并入融合。"""

    def fake_chunk(index, doc="doc_1", content="chunk content 内容"):
        return {
            "document_id": doc,
            "filename": f"{doc}.md",
            "content": content,
            "page_number": 1,
            "heading_path": "标题",
            "metadata": {"topic_label": "主题"},
            "chunk_index": index,
        }

    def federated_search(question, collections, top_k, document_ids=None):
        calls.append(question)
        if question == "go语言的数据类型":
            return [
                {"collection": collections[0], "index": 5, "score": 0.85, "chunk": fake_chunk(5, content="类型扩展块")},
            ], {"routing_strategy": "semantic_gate", "used_documents": []}
        return [
            {"collection": collections[0], "index": 0, "score": 0.9, "chunk": fake_chunk(0, content="主路答案块")},
            {"collection": collections[0], "index": 1, "score": 0.7, "chunk": fake_chunk(1, content="主路相关块")},
        ], {"routing_strategy": "semantic_gate", "used_documents": []}

    calls = []
    return SimpleNamespace(
        resolve_collections=lambda scope, ids: ids or ["rag_coll_1"],
        federated_search=federated_search,
        serialize_source=lambda item: item,
        document_catalog=lambda: [],
    ), calls


def test_tools_expansion_recall_merges_extra_paths():
    """扩展子问题独立成检索路并进融合；主路分更高仍优先。"""
    ctx = AgentChatContext()
    gateway, calls = _expansion_gateway()
    tools = {t.name: t for t in build_tools(ctx, gateway, expansion_queries=("go语言的数据类型",))}
    result = tools["search_library"].invoke("go语言怎么学")

    assert calls == ["go语言怎么学", "go语言的数据类型"]
    assert ctx.tool_calls[-1]["expansion_queries"] == ["go语言的数据类型"]
    assert ctx.fused[("rag_coll_1", 0)]["score"] == 0.9
    assert ctx.fused[("rag_coll_1", 5)]["score"] == 0.85  # 扩展路独有块进入融合
    assert "类型扩展块" in result


def test_tools_expansion_skips_duplicates():
    """扩展词与主路/副路相同则跳过，不重复检索。"""
    ctx = AgentChatContext()
    gateway, calls = _expansion_gateway()
    tools = {t.name: t for t in build_tools(ctx, gateway, expansion_queries=("go语言怎么学",))}
    tools["search_library"].invoke("go语言怎么学")
    assert calls == ["go语言怎么学"]  # 扩展词 == 主路 → 跳过
    assert ctx.tool_calls[-1]["expansion_queries"] == []


def test_stage_intent_passes_history_to_rewrite_and_expands(monkeypatch):
    """chat_history 从 stage_intent 透传给 rewrite_query；QUERY_EXPANSION_ENABLED 时算扩展。"""
    from backend.app.api import routes_retrieval as rr

    captured = {}

    def fake_rewrite(llm, q, history=None):
        captured["question"] = q
        captured["history"] = history
        return "go语言的变量声明"

    def fake_expand(llm, q, history=None):
        captured["expanded"] = q
        return ["go语言的数据类型"]

    monkeypatch.setattr(rr, "QUERY_REWRITE_ENABLED", True)
    monkeypatch.setattr(rr, "QUERY_EXPANSION_ENABLED", True)
    monkeypatch.setattr(rr, "rewrite_query", fake_rewrite)
    monkeypatch.setattr(rr, "expand_query", fake_expand)
    monkeypatch.setattr(
        rr, "route_query",
        lambda *a, **k: RouterDecision(scope="selected", document_ids=["doc_1"], rationale="路由"),
    )
    monkeypatch.setattr(rr, "_probe_and_escalate", lambda req, decision, q: (decision, False))

    history = [SimpleNamespace(type="human", content="go语言的数据类型是什么")]
    req = SimpleNamespace(question="它的变量声明", scope="auto", document_ids=[])
    intent = rr.stage_intent(req, _ToolCallingLLM({"scope": "selected", "document_ids": ["doc_1"]}), _fake_gateway(), None, history)

    assert captured["question"] == "它的变量声明"
    assert captured["history"] == history
    assert captured["expanded"] == "go语言的变量声明"  # 扩展基于改写后问题
    assert intent.expansions == ["go语言的数据类型"]
    assert intent.rewritten == "go语言的变量声明"


def test_stage_intent_no_expansion_when_disabled(monkeypatch):
    from backend.app.api import routes_retrieval as rr

    monkeypatch.setattr(rr, "QUERY_REWRITE_ENABLED", False)
    monkeypatch.setattr(rr, "QUERY_EXPANSION_ENABLED", False)
    monkeypatch.setattr(rr, "rewrite_query", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应调用改写")))
    monkeypatch.setattr(rr, "expand_query", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应调用扩展")))

    req = SimpleNamespace(question="问题", scope="selected", document_ids=["doc_1"])
    intent = rr.stage_intent(req, _fake_gateway(), None, None)
    assert intent.rewritten == "问题"
    assert intent.expansions == []
    assert intent.decision.scope == "selected"


def test_route_query_truncates_overlong_rationale():
    """LLM 生成超过 200 字符的路由理由时截断，避免 pydantic 校验失败中断路由。

    评估（eval_relational_react.py rq027）暴露：router 输出超长 rationale 时
    RouterDecision.model_validate 抛 string_too_long，整轮 /chat/agent 500。
    """
    long_rationale = "理" * 300
    llm = _ToolCallingLLM({"scope": "auto", "rationale": long_rationale})
    decision = route_query(llm, _fake_gateway(), "问题", [
        {"document_id": "doc_1", "filename": "a.md", "topic_label": "主题"},
    ])
    assert decision.scope == "auto"
    assert len(decision.rationale) == 200


def test_build_executor_escapes_braces_in_rationale():
    """路由理由含代码花括号（interface { Run() }）时，system prompt 能正常解析。

    评估（rq013/014/015）暴露：LLM 把代码片段写进 rationale 后拼进 system prompt，
    ChatPromptTemplate 把 { Run() } 当模板变量解析报 missing variables。
    """
    from langchain_core.prompts import ChatPromptTemplate

    from backend.app.rag.agent_rag import _build_system_prompt

    decision = RouterDecision(
        scope="auto",
        rationale="需要理解 type Animal interface { Run() } 的方法集与 struct{} 的关系",
    )
    system = _build_system_prompt(decision, None, "改写后的问题")
    # 组装结果里花括号是转义的安全形式（{{ }}），命名占位符已被 format 消费
    assert "{rationale}" not in system
    assert "{scope}" not in system
    # 与生产同构：从组装结果构造 ChatPromptTemplate 必须成功（原 bug 在此抛错），
    # 渲染后花括号还原为字面形式
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system),
            ("user", "{input}"),
        ]
    )
    messages = prompt.format_messages(input="问题")
    assert "interface { Run() }" in messages[0].content
    assert "struct{}" in messages[0].content


# ---------- Phase 5 澄清门控 ----------

def test_generate_clarification_questions_parses_lines():
    """LLM 返回多行澄清问题 → 解析出 2 条。"""
    llm = _CaptureLLM("你是想问切片的容量还是扩容机制？\n还是想问切片长度怎么算？")
    qs = generate_clarification_questions(llm, "切片问题", rewritten="切片问题", clues=[])
    assert len(qs) == 2
    assert "容量" in qs[0] and "扩容" in qs[0]


def test_generate_clarification_questions_uses_clues():
    """带线索时 prompt 包含线索文本（heading + content 前缀）与改写后问题。"""
    llm = _CaptureLLM("你是想问 A 还是 B？")
    generate_clarification_questions(
        llm, "问题", rewritten="改写后", clues=[_fused_item("coll", 0, 0.3, "切片的底层是数组")]
    )
    assert "切片的底层是数组" in llm.captured[0]
    assert "改写后" in llm.captured[0]


def test_generate_clarification_questions_no_clues_no_crash():
    """线索为空（no_evidence）→ 走占位文本，正常返回不崩。"""
    llm = _CaptureLLM("你是想问 A 还是 B？")
    qs = generate_clarification_questions(llm, "问题", clues=None)
    assert len(qs) == 1
    assert "（没有检索到线索）" in llm.captured[0]


def test_generate_clarification_questions_strips_dedupes_limits():
    """剥编号前缀、去重、最多 2 条。"""
    llm = _CaptureLLM("1.你是想问 A 还是 B？\n2.你是想问 A 还是 B？\n- 你是想问 C 还是 D？\n- 你是想问 E 还是 F？")
    qs = generate_clarification_questions(llm, "问题")
    assert qs == ["你是想问 A 还是 B？", "你是想问 C 还是 D？"]


def test_generate_clarification_questions_fallback():
    """空问题 / LLM 异常 → 回退 []。"""
    assert generate_clarification_questions(_CaptureLLM("x"), "") == []
    assert generate_clarification_questions(_BoomLLM(), "问题") == []


def test_run_clarification_gate_no_evidence_triggers(monkeypatch):
    from backend.app.api import routes_retrieval as rr

    monkeypatch.setattr(rr, "CLARIFICATION_GATE_ENABLED", True)
    gate = rr.run_clarification_gate(_CaptureLLM("你是想问A还是B？"), "问题", "改写后", [])
    assert gate is not None
    assert gate.triggered is True
    assert gate.reason == "no_evidence"
    assert gate.prompt == rr._CLARIFICATION_LEADINS["no_evidence"]
    assert len(gate.questions) == 1


def test_run_clarification_gate_weak_triggers(monkeypatch):
    from backend.app.api import routes_retrieval as rr

    monkeypatch.setattr(rr, "CLARIFICATION_GATE_ENABLED", True)
    gate = rr.run_clarification_gate(
        _CaptureLLM("你是想问A还是B？"), "问题", "改写后", [{"score": 0.3, "chunk": {}}]
    )
    assert gate is not None and gate.reason == "weak_evidence"


def test_run_clarification_gate_sufficient_skips(monkeypatch):
    from backend.app.api import routes_retrieval as rr

    monkeypatch.setattr(rr, "CLARIFICATION_GATE_ENABLED", True)
    assert (
        rr.run_clarification_gate(_CaptureLLM("x"), "问题", "改写后", [{"score": 0.8, "chunk": {}}])
        is None
    )


def test_run_clarification_gate_disabled_skips(monkeypatch):
    from backend.app.api import routes_retrieval as rr

    monkeypatch.setattr(rr, "CLARIFICATION_GATE_ENABLED", False)
    assert rr.run_clarification_gate(_CaptureLLM("x"), "问题", "改写后", []) is None


def test_run_clarification_gate_generation_empty_falls_back(monkeypatch):
    """LLM 生成空 → 仍触发，回退一条基于改写后/原问题的通用澄清（宁反问不硬凑）。"""
    from backend.app.api import routes_retrieval as rr

    monkeypatch.setattr(rr, "CLARIFICATION_GATE_ENABLED", True)
    monkeypatch.setattr(rr, "generate_clarification_questions", lambda *a, **k: [])
    gate = rr.run_clarification_gate(_CaptureLLM("x"), "问题", "改写后", [])
    assert gate is not None
    assert len(gate.questions) == 1
    assert "改写后" in gate.questions[0]


def test_judge_vague_question_true():
    """LLM 判 vague=true → True。"""
    llm = _CaptureLLM('{"vague": true, "reason": "问题太泛"}')
    assert judge_vague_question(llm, "go语言怎么学") is True


def test_judge_vague_question_false():
    """LLM 判 vague=false → False。"""
    llm = _CaptureLLM('{"vague": false, "reason": "主题明确"}')
    assert judge_vague_question(llm, "切片的容量如何扩容") is False


def test_judge_vague_question_parse_fail_false():
    """LLM 返回无法解析的内容 / 非布尔值 → False（不误伤正常题）。"""
    assert judge_vague_question(_CaptureLLM("x"), "问题") is False
    assert judge_vague_question(_CaptureLLM('{"vague": 1}'), "问题") is False


def test_judge_vague_question_boom_and_empty():
    """LLM 异常 / 空问题 → False。"""
    assert judge_vague_question(_BoomLLM(), "问题") is False
    assert judge_vague_question(_CaptureLLM("x"), "") is False


def test_run_clarification_gate_sufficient_but_vague_triggers(monkeypatch):
    """证据充分但 LLM 判定问题模糊 → 触发，reason=vague，走 vague 引导文案。"""
    from backend.app.api import routes_retrieval as rr

    monkeypatch.setattr(rr, "CLARIFICATION_GATE_ENABLED", True)
    monkeypatch.setattr(rr, "judge_vague_question", lambda *a, **k: True)
    gate = rr.run_clarification_gate(
        _CaptureLLM("你是想问A还是B？"), "问题", "改写后", [{"score": 0.8, "chunk": {}}]
    )
    assert gate is not None
    assert gate.triggered is True
    assert gate.reason == "vague"
    assert gate.vague is True
    assert gate.prompt == rr._CLARIFICATION_LEADINS["vague"]
    assert len(gate.questions) == 1


def test_run_clarification_gate_sufficient_not_vague_skips(monkeypatch):
    """证据充分且 LLM 判定不模糊 → 不触发（正常作答）。"""
    from backend.app.api import routes_retrieval as rr

    monkeypatch.setattr(rr, "CLARIFICATION_GATE_ENABLED", True)
    monkeypatch.setattr(rr, "judge_vague_question", lambda *a, **k: False)
    assert (
        rr.run_clarification_gate(_CaptureLLM("x"), "问题", "改写后", [{"score": 0.8, "chunk": {}}])
        is None
    )


class _ConfigCaptureLLM(_CaptureLLM):
    """带 with_config（chat_agent 里 llm.with_config 挂 token 回调）。"""

    def with_config(self, config):
        return self


class _FakeMetrics:
    def incr(self, *a, **k):
        pass

    def observe(self, *a, **k):
        pass


def test_chat_agent_clarification_path_skips_persist_and_profile(monkeypatch):
    """证据不足时 chat_agent 返回澄清轮：answer=引导文案、sources 空、persist/profile 不执行。"""
    from backend.app.api import routes_retrieval as rr

    fake_gate = SimpleNamespace(
        triggered=True,
        reason="weak_evidence",
        questions=["你是想问切片的容量还是扩容机制？"],
        prompt="找到的相关内容比较有限，为了避免答偏，请先确认一下你想问的是：",
    )

    def fake_intent(req, llm, gateway, profile, history):
        return SimpleNamespace(
            rewritten="改写后", expansions=[], escalated=False,
            decision=RouterDecision(scope="auto", rationale="路由"), ms=1.0,
        )

    def fake_react(req, llm, gateway, decision, rewritten, history, profile, expansions):
        return SimpleNamespace(
            answer="硬凑答案", ms=2.0,
            ranked=[{"score": 0.3, "chunk": {"content": "切片"}}],
            ctx=SimpleNamespace(
                fused={},
                tool_calls=[{"tool": "search_library", "question": "改写后", "document_ids": []}],
            ),
            result={"intermediate_steps": []},
        )

    monkeypatch.setattr(rr, "check_chat_rate_limit", lambda uid: None)
    monkeypatch.setattr(rr, "_validate_model", lambda m: m or "deepseek-v4-flash")
    monkeypatch.setattr(rr, "_get_agent_llm", lambda m: _ConfigCaptureLLM("你是想问A还是B？"))
    monkeypatch.setattr(rr, "TokenUsageCallback", lambda: SimpleNamespace(input_tokens=0, output_tokens=0))
    monkeypatch.setattr(rr, "_build_gateway", lambda: _fake_gateway())
    monkeypatch.setattr(rr, "_profile_for", lambda uid: None)
    monkeypatch.setattr(rr, "_prepare_conversation", lambda uid, req: ("conv_1", []))
    monkeypatch.setattr(rr, "stage_intent", fake_intent)
    monkeypatch.setattr(rr, "stage_react", fake_react)
    monkeypatch.setattr(rr, "run_clarification_gate", lambda llm, q, rewritten, ranked: fake_gate)
    monkeypatch.setattr(rr, "get_metrics", lambda: _FakeMetrics())
    monkeypatch.setattr(rr, "stage_persist", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应落库")))
    monkeypatch.setattr(rr, "stage_profile", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应画像")))

    req = SimpleNamespace(
        question="问题", scope="auto", document_ids=[], top_k=5,
        model=None, conversation_id=None,
    )
    resp = rr.chat_agent(req, {"id": "u1"})
    assert resp["answer"] == fake_gate.prompt
    assert resp["sources"] == []
    assert resp["used_documents"] == []
    assert resp["conversation_id"] == "conv_1"
    assert resp["retrieval"]["evidence"]["sufficient"] is False
    assert resp["retrieval"]["evidence"]["reason"] == "weak_evidence"
    assert resp["retrieval"]["evidence"]["clarification"]["questions"] == fake_gate.questions
    assert resp["retrieval"]["stages"]["persist_ms"] == 0
    assert resp["retrieval"]["stages"]["profile_ms"] == 0
