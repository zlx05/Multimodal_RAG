from backend.app.rag.document_registry import document_collection_name


def test_document_collection_name_is_shared_constant():
    # 单库迁移后所有文档共用一个 collection，名恒为 rag_all（文档身份靠 chunk 上的 document_id 分区）
    first = document_collection_name("doc_abc123", "data_structures.md")
    second = document_collection_name("doc_def456", "data_structures.md")

    assert first == "rag_all"
    assert second == "rag_all"
