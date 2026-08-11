"""重建全部 HTML 资料索引（读 MySQL documents 表，非磁盘 JSON registry）。

背景：HTML 解析器把同一 ul/ol 的相邻 <li> 合并为一个块（避免"可比较类型有：
布尔/数字/字符串…"这类枚举被切碎成单行 chunk，导致 agent 反复补检、被
max_iterations 截断）。已入库的 HTML collection 是用旧 parser 建的，需要
用新 parser 重新解析 + 分块 + 重建，让列表枚举原子化。

用法（从仓库根目录运行）:
    D:/mnist_data/ancanda/envs/rag11/python.exe scripts/rebuild_html.py [--only doc_xxx]
"""
import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.core.config import RAG_ORIGINAL_DIR, RAG_WORK_DIR, FORMULA_RECOGNITION_ENABLED
from backend.app.rag.chunking_profiles import resolve_profile
from backend.app.rag.chunkers import get_chunker
from backend.app.rag.hybrid_pipeline import HybridRAGPipeline
from backend.app.rag.document_registry import _all_records


def _vision_analyzer():
    from backend.app.rag.vision import VisionAnalyzer

    return VisionAnalyzer()


def _formula_recognizer():
    if not FORMULA_RECOGNITION_ENABLED:
        return None
    from backend.app.rag.ocr.formula_engine import create_formula_recognizer

    return create_formula_recognizer()


def _parse_document(document_id: str, source_path: str, filename: str):
    """复刻 worker._parse：create_parser + 补文件名元数据。"""
    from backend.app.rag.parsers import create_parser

    parser = create_parser(
        document_id,
        source_path,
        original_dir=RAG_ORIGINAL_DIR,
        work_dir=RAG_WORK_DIR,
        vision_analyzer=_vision_analyzer(),
        formula_recognizer=_formula_recognizer(),
    )
    blocks = parser.parse(source_path)
    for block in blocks:
        block.metadata["filename"] = filename
        block.metadata["document_name"] = filename
    if not blocks:
        raise ValueError(f"解析结果为空: {source_path}")
    return blocks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default=None, help="只重建指定 document_id")
    args = parser.parse_args()

    records = _all_records()
    docs = [
        rec for rec in records.values()
        if str(rec.get("filename", "")).lower().endswith((".html", ".htm"))
    ]
    if args.only:
        docs = [rec for rec in docs if rec.get("document_id") == args.only]
        if not docs:
            print(f"未找到 HTML 文档: {args.only}")
            return
    docs.sort(key=lambda r: str(r.get("filename", "")))
    print(f"MySQL HTML 文档 {len(docs)} 份，开始重建\n")

    from pymilvus import connections, utility

    connections.connect(alias="default", host="127.0.0.1", port="19530")

    for rec in docs:
        doc_id = rec["document_id"]
        filename = rec.get("filename", "")
        src = rec.get("source_path", "")
        name = rec.get("collection_name", "")
        if not src or not Path(src).exists():
            print(f"  [SKIP] {doc_id} 源文件不存在: {src}")
            continue
        try:
            blocks = _parse_document(doc_id, src, filename)
            profile = resolve_profile("auto", filename, blocks)
        except Exception as exc:
            print(f"  [ERROR] {doc_id} {filename} 解析失败: {exc}")
            continue

        print(f"  [REBUILD] {doc_id} {filename} profile={profile.id} ...", flush=True)
        if name and utility.has_collection(name):
            utility.drop_collection(name)
        pipe = HybridRAGPipeline(name, "", milvus_host="127.0.0.1", milvus_port="19530", with_llm=False)
        params = dict(profile.params)
        if profile.chunker == "semantic":
            params["embedder"] = pipe.embedder
        chunker = get_chunker(profile.chunker, **params)
        n = pipe.build(blocks, chunker, profile=profile)
        print(f"    -> 入库 {n} chunks", flush=True)

    print("\n== 重建完成 ==")


if __name__ == "__main__":
    main()
