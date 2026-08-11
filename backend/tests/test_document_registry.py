from backend.app.rag.document_registry import document_collection_name


def test_document_collection_name_is_readable_and_unique():
    first = document_collection_name("doc_abc123", "data_structures.md")
    second = document_collection_name("doc_def456", "data_structures.md")

    assert first == "rag_data_structures_abc123"
    assert second != first
    assert first.startswith("rag_")
