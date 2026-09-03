"""单库迁移：每文档一 Collection → 单共享 Collection `rag_all` + document_id 分区。

读取全部旧 `rag_*` collection 的 chunk（复用已有 embedding，保证与迁移前检索
行为完全一致），按 document_id 分区写入 rag_all；校验 num_entities 后 drop 旧
collection；并把 MySQL documents.collection_name 与 data/document_registry.json
同步为 "rag_all"。幂等，可安全重跑（每文档先删后插）。

用法（从仓库根目录运行）:
    conda activate rag11
    python scripts/migrate_single_collection.py --dry-run   # 只打印计划，不写
    python scripts/migrate_single_collection.py             # 执行迁移

安全约束：绝不 drop rag_all 本身；drop 旧库只发生在断言通过之后。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pymilvus import Collection, utility  # noqa: E402

from backend.app.rag.catalog import connect_milvus  # noqa: E402
from backend.app.rag.hybrid_pipeline import (  # noqa: E402
    INDEX_BATCH_SIZE,
    SHARED_COLLECTION,
    HybridRAGPipeline,
)

# build() 的 insert 列序（不含 auto id），见 hybrid_pipeline.build()
INSERT_COLUMNS = [
    "document_id",
    "filename",
    "source_type",
    "content_type",
    "page_number",
    "chunk_index",
    "content",
    "heading_path",
    "image_path",
    "bbox",
    "confidence",
    "metadata",
    "embedding",
]

REGISTRY_JSON = PROJECT_ROOT / "data/document_registry.json"


def _query_all_rows(collection: Collection) -> list[dict]:
    """读某旧 collection 的全部行（含 embedding），并校验没有因 limit 截断。"""
    fields = [f.name for f in collection.schema.fields if f.name != "id"]
    rows = collection.query(expr="chunk_index >= 0", output_fields=fields)
    if len(rows) != collection.num_entities:
        raise RuntimeError(
            f"{collection.name}: query 返回 {len(rows)} 行 != num_entities "
            f"{collection.num_entities}，疑似 Milvus query 默认 limit 截断，中止"
        )
    return rows


def _normalize_row(row: dict) -> dict:
    """规范化字段类型，保证可直接插入 rag_all（与 build() 产出同构）。"""
    doc_id = str(row.get("document_id") or "").strip()
    if not doc_id:
        raise RuntimeError(f"行缺少 document_id: {str(row)[:200]}")
    return {
        "document_id": doc_id,
        "filename": str(row.get("filename") or ""),
        "source_type": str(row.get("source_type") or ""),
        "content_type": str(row.get("content_type") or ""),
        "page_number": int(row.get("page_number") or 0),
        "chunk_index": int(row.get("chunk_index") or 0),
        "content": str(row.get("content") or ""),
        "heading_path": str(row.get("heading_path") or ""),
        "image_path": str(row.get("image_path") or ""),
        "bbox": list(row.get("bbox") or []),
        "confidence": float(row.get("confidence") or -1.0),
        "metadata": dict(row.get("metadata") or {}),
        "embedding": list(row.get("embedding") or []),
    }


def _collect_plan() -> tuple[list[str], dict[str, list[dict]], dict[str, int]]:
    """枚举旧 rag_* 库，读全部行，按 document_id 分组。

    返回 (旧库名列表, doc_id->rows, doc_id->旧chunk数)。空库只进旧库名列表。
    """
    old_collections = sorted(
        c for c in utility.list_collections() if c.startswith("rag_") and c != SHARED_COLLECTION
    )
    if not old_collections:
        print("[migrate] 没有旧 rag_* collection，无需迁移")
        sys.exit(0)

    grouped: dict[str, list[dict]] = {}
    before: dict[str, int] = {}
    empty: list[str] = []
    for name in old_collections:
        col = Collection(name)
        if col.num_entities == 0:
            empty.append(name)
            print(f"  [空] {name}：0 chunks（仅清理）")
            continue
        rows = _query_all_rows(col)
        rows = [_normalize_row(r) for r in rows]
        for row in rows:
            grouped.setdefault(row["document_id"], []).append(row)
        before[row["document_id"]] = before.get(row["document_id"], 0) + len(rows)
        print(f"  [读] {name}: {len(rows)} chunks → doc {row['document_id']}")
    print(f"  [空库] {len(empty)} 个：{', '.join(empty) if empty else '无'}")
    return old_collections, grouped, before


def _insert_doc(pipeline: HybridRAGPipeline, document_id: str, rows: list[dict]) -> None:
    """幂等：先删 rag_all 中该文档旧 chunk，再按 build() 列序批量插入（复用 embedding）。"""
    pipeline.collection.delete(expr=f'document_id == "{document_id}"')
    for start in range(0, len(rows), INDEX_BATCH_SIZE):
        batch = rows[start : start + INDEX_BATCH_SIZE]
        columns = [col for col in INSERT_COLUMNS if col != "embedding"]
        data = [[row[c] for row in batch] for c in columns]
        data.append([row["embedding"] for row in batch])
        pipeline.collection.insert(data)


def _update_mysql() -> int:
    from backend.app.core.database import SessionLocal
    from backend.app.db.models import Document

    with SessionLocal() as db:
        changed = db.query(Document).filter(Document.collection_name != SHARED_COLLECTION).update(
            {Document.collection_name: SHARED_COLLECTION}, synchronize_session=False
        )
        db.commit()
        total = db.query(Document).count()
    print(f"[migrate] MySQL documents.collection_name → {SHARED_COLLECTION}: 更新 {changed}/{total} 行")
    return changed


def _update_registry_json() -> int:
    if not REGISTRY_JSON.exists():
        print("[migrate] data/document_registry.json 不存在，跳过")
        return 0
    with open(REGISTRY_JSON, encoding="utf-8") as fh:
        registry = json.load(fh)
    changed = 0
    for rec in registry.values():
        if rec.get("collection_name") != SHARED_COLLECTION:
            rec["collection_name"] = SHARED_COLLECTION
            changed += 1
    with open(REGISTRY_JSON, "w", encoding="utf-8") as fh:
        json.dump(registry, fh, ensure_ascii=False, indent=2)
    print(f"[migrate] data/document_registry.json collection_name → {SHARED_COLLECTION}: 更新 {changed} 条")
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description="单库迁移：旧 rag_* → rag_all")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写 Milvus/MySQL/JSON")
    args = parser.parse_args()

    connect_milvus()
    # 构造 pipeline：连接 Milvus + 创建/校验 rag_all + 载入共享 embedding 模型
    pipeline = HybridRAGPipeline(SHARED_COLLECTION, with_llm=False)
    expected_dim = pipeline.embedder.get_sentence_embedding_dimension()

    print(f"[migrate] 读取旧 rag_* collection（目标: {SHARED_COLLECTION}, dim={expected_dim}）…")
    old_collections, grouped, before = _collect_plan()

    total_chunks = sum(len(rows) for rows in grouped.values())
    n_docs = len(grouped)
    print(f"\n[计划] 共 {n_docs} 个文档, {total_chunks} chunks → {SHARED_COLLECTION}")

    # 校验所有行 embedding 维度与目标一致
    for doc_id, rows in sorted(grouped.items()):
        for row in rows:
            if len(row["embedding"]) != expected_dim:
                raise RuntimeError(
                    f"doc {doc_id} 行 embedding 维度 {len(row['embedding'])} != 目标 {expected_dim}"
                )
    print(f"[校验] 所有行 embedding 维度 = {expected_dim} ✓")

    if args.dry_run:
        print("\n[dry-run] 未写入任何数据。per-doc 计划：")
        for doc_id in sorted(grouped):
            print(f"  {doc_id}: {before[doc_id]} chunks")
        print(f"  旧库待 drop: {len(old_collections)} 个")
        sys.exit(0)

    # 1. 写入 rag_all（每文档幂等先删后插）
    for doc_id, rows in sorted(grouped.items()):
        _insert_doc(pipeline, doc_id, rows)
        print(f"  [写入] {doc_id}: {len(rows)} chunks")

    pipeline.collection.flush()
    pipeline.collection.load()

    # 2. 断言：rag_all 实体数 == 拷贝总数（绝不在断言前 drop 旧库）
    actual = pipeline.collection.num_entities
    if actual != total_chunks:
        raise RuntimeError(
            f"断言失败: rag_all num_entities={actual} != 期望 {total_chunks}。旧库保持不动，请检查后重跑。"
        )
    print(f"[断言] rag_all.num_entities == {total_chunks} ✓")

    # 3. 同步 MySQL + JSON registry
    _update_mysql()
    _update_registry_json()

    # 4. drop 旧库（此时 rag_all 已断言就绪）
    for name in old_collections:
        if utility.has_collection(name):
            utility.drop_collection(name)
            print(f"  [drop] {name}")
    print(f"[drop] 旧库清理完成，剩 {len(utility.list_collections())} 个 collection（含 rag_all）")

    # 5. 全表重载 BM25（单库全局 IDF），打印 per-doc 报告
    pipeline._load_bm25_from_milvus()
    after: dict[str, int] = {}
    for row in pipeline._chunk_pool:
        after[row["document_id"]] = after.get(row["document_id"], 0) + 1
    print("\n[报告] per-doc chunk 数（迁移前 → 迁移后）:")
    for doc_id in sorted(set(before) | set(after)):
        print(f"  {doc_id}: {before.get(doc_id, 0)} → {after.get(doc_id, 0)}")
    print(f"\n[完成] rag_all 共 {pipeline.collection.num_entities} chunks。")
    print("提示: API/worker 若在跑请重启（清 _pipeline_cache / self._pipelines）；本机当前未在跑。")


if __name__ == "__main__":
    main()
