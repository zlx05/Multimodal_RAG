from backend.app.rag.hybrid.bm25_store import BM25Store


def test_bm25_does_not_return_question_word_only_matches():
    store = BM25Store()
    store.build([
        "字面量是用于表达源代码中一个固定值的符号。",
        "张林翔的学号是 2024211802。",
    ])

    results = store.search("张林翔的学号是什么", top_k=5)

    assert [item["index"] for item in results] == [1]
