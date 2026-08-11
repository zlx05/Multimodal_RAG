from backend.app.api.routes_retrieval import (
    _expand_learning_question,
    _heading_matches_intent,
    _query_entity_anchors,
    _strong_lexical_match,
)


def test_visibility_question_expands_to_source_terms():
    expanded = _expand_learning_question("Go 语言怎么判断其他包能不能访问变量？")

    assert "可见性" in expanded
    assert "公有" in expanded
    assert "私有" in expanded
    assert "大写" in expanded
    assert "小写" in expanded


def test_visibility_heading_is_boostable():
    assert _heading_matches_intent("全局变量和其他包访问 可见性", "基本语法 > 包 > 可见性")
    assert not _heading_matches_intent("Go 函数参数", "基本语法 > 函数")


def test_question_words_are_not_strong_matches():
    text = "字面量是用于表达源代码中一个固定值的符号，也叫字面值。"

    assert not _strong_lexical_match("张林翔的学号是什么", text)


def test_entity_question_extracts_subject():
    assert _query_entity_anchors("张林翔的学号是什么") == {"张林翔"}
