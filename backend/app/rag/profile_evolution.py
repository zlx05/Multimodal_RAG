"""画像进化（Phase 2C）：每次长回答后，由 LLM 判断学生的行为/性格/薄弱点/风格倾向，
动态更新 user_profiles + user_memory。

画像不是只有初始的回答倾向（调查问卷）；它随对话持续演化：
- 薄弱点：去重合并进画像（profile_version++），回答时优先覆盖；
- 行为/性格观察：写 user_memory（memory_type="personality"）留痕，可查可删；
- 风格倾向：连续 STYLE_CONSENSUS 次一致信号且与当前不同时才漂移 preferred_style（防抖动）。

**软失败**：任何异常（LLM 调用失败、DB 不可用、解析失败）都不影响正常问答。
"""

import json
import re

from ..db import org

# 画像进化的可接受值
STYLE_VALUES = {"direct", "guiding", "socratic"}
# 风格漂移需要的连续一致信号数（防抖动）
STYLE_CONSENSUS = 2
# 风格信号在 user_memory 里的标记（放在 source_question，免加列）
_STYLE_SIGNAL = "风格信号"


def _extract_json(text: str) -> dict | None:
    """宽容解析 LLM 返回的 JSON。

    DeepSeek 兼容端点不支持 response_format，正文可能带说明文字或围栏，
    这里先试整段 JSON，再退到第一个 {...} 块。
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except ValueError:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except ValueError:
            pass
    return None


_JUDGE_PROMPT = """你是一位学习陪伴助手，负责从一段师生问答中观察学生，更新他的学习画像。
只输出一个 JSON 对象，不要输出任何其他文字。字段：
- "weak_points": 学生暴露出的薄弱点或易错点，字符串数组；没有则为 []
- "style_tendency": 这轮问答中学生表现出的答题偏好，"direct"(想要直接答案) /
  "guiding"(想要解题思路) / "socratic"(喜欢被引导自己思考)，不明显则为 null
- "behaviors": 对学生行为、性格、学习习惯的观察，每条一句简短中文；没有则为 []

学生问题：{question}

回答：{answer}

输出 JSON："""


def judge_user(llm, question: str, answer: str) -> dict:
    """一次 LLM 调用，返回 {weak_points, style_tendency, behaviors}；解析失败返回空值。"""
    prompt = _JUDGE_PROMPT.format(question=question, answer=str(answer)[:4000])
    try:
        response = llm.invoke(prompt)
    except Exception as exc:
        print(f"[profile_evolution] 画像判断调用失败（软失败）: {exc}")
        return {"weak_points": [], "style_tendency": None, "behaviors": []}
    data = _extract_json(getattr(response, "content", response))
    if not data:
        return {"weak_points": [], "style_tendency": None, "behaviors": []}
    weak_points = [str(item).strip() for item in data.get("weak_points", []) if str(item).strip()]
    tendency = data.get("style_tendency")
    tendency = tendency if tendency in STYLE_VALUES else None
    behaviors = [str(item).strip() for item in data.get("behaviors", []) if str(item).strip()]
    return {"weak_points": weak_points, "style_tendency": tendency, "behaviors": behaviors}


def _recent_style_signals(user_id: str, limit: int = STYLE_CONSENSUS) -> list[str]:
    """最近 limit 条风格信号（user_memory 按 created_at 倒序，取最新 limit 再转正序）。"""
    signals = [
        m["content"]
        for m in org.list_memory(user_id)
        if m["memory_type"] == "preference" and m.get("source_question") == _STYLE_SIGNAL
    ]
    return signals[:limit][::-1]


def apply_profile_evolution(user_id: str, llm, question: str, answer: str) -> None:
    """画像进化入口。无画像跳过；任何异常软失败（不影响正常问答）。"""
    try:
        profile = org.get_profile(user_id)
        if profile is None:
            return

        judgment = judge_user(llm, question, answer)

        if judgment["weak_points"]:
            org.merge_profile_weak_points(user_id, judgment["weak_points"])
        for behavior in judgment["behaviors"]:
            org.add_memory(
                user_id, "personality", behavior,
                source_question=str(question)[:120], confidence=0.8,
            )

        tendency = judgment["style_tendency"]
        if tendency:
            org.add_memory(
                user_id, "preference", tendency, source_question=_STYLE_SIGNAL, confidence=0.7,
            )
            signals = _recent_style_signals(user_id)
            current = profile.get("preferred_style", "standard")
            if (
                len(signals) >= STYLE_CONSENSUS
                and all(signal == tendency for signal in signals)
                and tendency != current
            ):
                org.upsert_profile(user_id, preferred_style=tendency)
    except Exception as exc:
        print(f"[profile_evolution] 画像更新失败（软失败）: {exc}")
