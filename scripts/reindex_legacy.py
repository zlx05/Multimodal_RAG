"""重建 legacy 索引（旧版入库丢过 <100 字符的短 section）。

根因：旧版入库用 MarkdownChunker 默认 min_chunk_size=100 且不加标题前缀，
所有 <100 字符的 section（如 AVL 旋转 84 字符、顺序查找 ASL 38 字符）被静默丢弃。
当前代码（technical profile min=40 + build() 的 fallback）能保留全部内容。

用法：
    python scripts/reindex_legacy.py --dry-run   # 只解析 + 分块，报告期望 chunk 数，不动 Milvus
    python scripts/reindex_legacy.py --rebuild   # 对 legacy collection drop + 重建
    python scripts/reindex_legacy.py --rebuild --all  # 重建所有文档（含已 current 的）
"""
import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.core.config import RAG_ORIGINAL_DIR, RAG_WORK_DIR, FORMULA_RECOGNITION_ENABLED
from backend.app.rag.chunking_profiles import resolve_profile
from backend.app.rag.chunkers import get_chunker
from backend.app.rag.hybrid_pipeline import HybridRAGPipeline


def _vision_analyzer():
    from backend.app.rag.vision import VisionAnalyzer

    return VisionAnalyzer()


def _formula_recognizer():
    if not FORMULA_RECOGNITION_ENABLED:
        return None
    from backend.app.rag.ocr.formula_engine import create_formula_recognizer

    return create_formula_recognizer()


def _parse_document(document_id: str, source_path: str, filename: str = ""):
    """复刻 worker._parse：create_parser + 补文件名元数据。"""
    from backend.app.rag.parsers import create_parser

    parser_kwargs = {
        "original_dir": RAG_ORIGINAL_DIR,
        "work_dir": RAG_WORK_DIR,
        "vision_analyzer": _vision_analyzer(),
        "formula_recognizer": _formula_recognizer(),
    }
    parser = create_parser(document_id, source_path, **parser_kwargs)
    blocks = parser.parse(source_path)
    filename = filename or Path(source_path).name
    for block in blocks:
        block.metadata["filename"] = filename
        block.metadata["document_name"] = filename
    if not blocks:
        raise ValueError(f"解析结果为空: {source_path}")
    return blocks


def _chunk_count(blocks, profile):
    """只统计期望 chunk 数（复刻 build() 的过滤逻辑），不加载 embedder。"""
    from backend.app.rag.chunking_profiles import group_blocks_for_profile

    chunker = get_chunker(profile.chunker, **dict(profile.params))
    grouped = group_blocks_for_profile(blocks, profile)
    total = 0
    for block in grouped:
        text = block.text
        if not text or not text.strip():
            continue
        chunks = chunker.chunk(text)
        if not chunks:
            chunks = [text]
        total += sum(1 for c in chunks if c and c.strip())
    return total


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只解析分块报告，不动 Milvus")
    parser.add_argument("--rebuild", action="store_true", help="drop + 重建 legacy collection")
    parser.add_argument("--all", action="store_true", help="重建所有文档（含已 current 的）")
    parser.add_argument("--only", default=None, help="只重建指定 document_id")
    args = parser.parse_args()
    if not (args.dry_run or args.rebuild):
        parser.error("请指定 --dry-run 或 --rebuild")

    registry = json.load(open(PROJECT_ROOT / "data/document_registry.json", encoding="utf-8"))
    docs = list(registry.values())
    print(f"注册文档 {len(docs)} 份\n")

    # 探测每个 collection 是否 legacy（metadata 有无 chunk_profile）
    from pymilvus import connections, utility, Collection

    connections.connect(alias="default", host="127.0.0.1", port="19530")
    legacy_map = {}
    for rec in docs:
        name = rec.get("collection_name", "")
        if not name or not utility.has_collection(name):
            legacy_map[rec["document_id"]] = False
            continue
        try:
            c = Collection(name)
            res = c.query(expr="chunk_index >= 0", limit=1, output_fields=["metadata"])
            md = (res[0].get("metadata") or {}) if res else {}
            legacy_map[rec["document_id"]] = not bool(md.get("chunk_profile"))
        except Exception:
            legacy_map[rec["document_id"]] = False

    if args.only:
        docs = [rec for rec in docs if rec.get("document_id") == args.only]
        if not docs:
            print(f"未找到 document_id: {args.only}")
            return

    results = []
    for rec in docs:
        doc_id = rec["document_id"]
        src = rec.get("source_path", "")
        filename = rec.get("filename", "")
        name = rec.get("collection_name", "")
        if not src or not Path(src).exists():
            print(f"  [SKIP] {doc_id} 源文件不存在: {src}")
            continue
        is_legacy = legacy_map.get(doc_id, False)
        if args.rebuild and not args.all and not is_legacy:
            print(f"  [SKIP] {doc_id} 已是 current，跳过")
            continue
        try:
            blocks = _parse_document(doc_id, src, filename)
            profile = resolve_profile("auto", filename, blocks)
            expected = _chunk_count(blocks, profile)
            actual = None
            if name and utility.has_collection(name):
                actual = Collection(name).num_entities
            results.append((doc_id, filename, profile.id, expected, actual, is_legacy, blocks, profile, name))
            print(f"  [PARSE] {doc_id} {filename} profile={profile.id} 期望chunk={expected} 现存={actual} legacy={is_legacy}")
        except Exception as exc:
            print(f"  [ERROR] {doc_id} {filename}: {exc}")

    if not args.rebuild:
        print("\n== dry-run 完成，未改动 Milvus。加 --rebuild 重建 ==")
        return

    print("\n== 开始重建 ==")
    for doc_id, filename, profile_id, expected, actual, is_legacy, blocks, profile, name in results:
        print(f"  [REBUILD] {doc_id} {filename} ...", flush=True)
        # 先 drop 旧 collection，用当前 pipeline 重建（与 worker._index 同逻辑）
        if name and utility.has_collection(name):
            utility.drop_collection(name)
        pipe = HybridRAGPipeline(name, "", milvus_host="127.0.0.1", milvus_port="19530", with_llm=False)
        params = dict(profile.params)
        if profile.chunker == "semantic":
            params["embedder"] = pipe.embedder
        chunker = get_chunker(profile.chunker, **params)
        n = pipe.build(blocks, chunker, profile=profile)
        print(f"    -> 入库 {n} chunks（期望 {expected}）", flush=True)
    print("== 重建完成 ==")


if __name__ == "__main__":
    main()
