"""用户画像注入测试：agent_rag 的 profile 参数不破坏旧签名，且能改回答风格。

route_query / build_executor 的 profile 是可选参数，默认 None → 旧行为不变。
用 fake llm 捕获 prompt 验证画像块确实被拼进去。
"""

from types import SimpleNamespace

from langchain_core.runnables import RunnableLambda

from backend.app.rag.agent_rag import (
    AgentChatContext,
    RouterDecision,
    _profile_block,
    build_executor,
    route_query,
)


BEGINNER = {"subjects": ["数学"], "weak_points": ["导数"], "preferred_style": "beginner"}
ADVANCED = {"subjects": ["高数"], "weak_points": [], "preferred_style": "advanced"}


def _fake_gateway():
    return SimpleNamespace(
        resolve_collections=lambda scope, ids: ids or ["rag_coll_1"],
        federated_search=lambda *a, **k: ([], {"routing_strategy": "none"}),
        serialize_source=lambda item: item,
        document_catalog=lambda: [{"document_id": "doc_1", "filename": "a.md", "topic_label": "主题"}],
    )


def _prompt_capturing_llm():
    """bind_tools 的 invoke 返回空 tool_calls，但把 prompt 记下来（captured 跨调用共享）。"""
    captured = []

    def bind_tools(_schema):
        def invoke(prompt):
            captured.append(prompt)
            return SimpleNamespace(tool_calls=None)

        return SimpleNamespace(invoke=invoke)

    return SimpleNamespace(bind_tools=bind_tools, captured=captured)


class _FakeLLM(RunnableLambda):
    """build_executor 需要 Runnable + bind_tools 才能通过 `|` 组装；从不 invoke。"""

    def __init__(self):
        super().__init__(lambda x: x)

    def bind_tools(self, tools):
        return self


def test_profile_block_empty_for_none():
    assert _profile_block(None) == ""
    assert _profile_block({}) == ""


def test_profile_block_beginner_style():
    block = _profile_block(BEGINNER)
    assert "初学者" in block
    assert "步骤化" in block
    assert "导数" in block  # 薄弱点拼入


def test_profile_block_advanced_style():
    block = _profile_block(ADVANCED)
    assert "基础较好" in block
    assert "推导和反例" in block
    assert "初学者" not in block


def test_profile_block_new_styles():
    direct = _profile_block({"preferred_style": "direct"})
    assert "直接给答案" in direct and "结论先行" in direct
    guiding = _profile_block({"preferred_style": "guiding"})
    assert "先给思路" in guiding
    socratic = _profile_block({"preferred_style": "socratic"})
    assert "循循善诱" in socratic and "递进式提问" in socratic


def test_profile_block_legacy_style_fallback():
    # 存量旧值仍走对应文案（Phase 2C 兼容），未知值走默认完整精炼
    assert "初学者" in _profile_block({"preferred_style": "beginner"})
    assert "推导和反例" in _profile_block({"preferred_style": "advanced"})
    assert "精炼" in _profile_block({"preferred_style": "unknown-xyz"})


def test_route_query_injects_subjects_with_profile():
    fake = _prompt_capturing_llm()
    route_query(fake, _fake_gateway(), "什么是极限", [], profile=BEGINNER)
    assert "关注科目" in fake.captured[0] and "数学" in fake.captured[0]


def test_route_query_without_profile_matches_old_prompt():
    fake = _prompt_capturing_llm()
    route_query(fake, _fake_gateway(), "什么是极限", [])
    assert "关注科目" not in fake.captured[0]


def _executor_system_content(executor) -> str:
    """从 executor 的 runnable 管线里取出 system 消息文本。"""
    prompt = executor.agent.runnable.steps[1]  # ChatPromptTemplate
    msgs = prompt.invoke({"chat_history": [], "input": "", "agent_scratchpad": []})
    return msgs.messages[0].content


def test_build_executor_injects_profile_block():
    ctx = AgentChatContext()
    executor = build_executor(_FakeLLM(), ctx, _fake_gateway(), RouterDecision(), profile=ADVANCED)
    assert "推导和反例" in _executor_system_content(executor)


def test_build_executor_without_profile_is_backward_compatible():
    ctx = AgentChatContext()
    executor = build_executor(_FakeLLM(), ctx, _fake_gateway(), RouterDecision())
    assert "用户画像" not in _executor_system_content(executor)


def test_build_executor_injects_rewritten_hint():
    ctx = AgentChatContext()
    executor = build_executor(
        _FakeLLM(), ctx, _fake_gateway(), RouterDecision(), rewritten_question="极限的定义是什么"
    )
    content = _executor_system_content(executor)
    assert "检索提示" in content
    assert "极限的定义是什么" in content


def test_build_executor_without_rewritten_has_no_hint():
    ctx = AgentChatContext()
    executor = build_executor(_FakeLLM(), ctx, _fake_gateway(), RouterDecision())
    assert "检索提示" not in _executor_system_content(executor)
