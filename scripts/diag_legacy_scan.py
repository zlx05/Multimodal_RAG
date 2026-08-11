"""全库扫描：找出 legacy 索引（metadata 无 chunk_profile 的 collection）。

当前 hybrid build 一定会写 metadata.chunk_profile；没有该字段的 collection
是旧版代码建的，可能因 min_chunk_size 默认 100 丢过短 section。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import json
from pymilvus import connections, utility, Collection

connections.connect(alias="default", host="127.0.0.1", port="19530")

# 文档注册表：document_id -> 记录
registry = json.load(open(PROJECT_ROOT / "data/document_registry.json", encoding="utf-8"))
id_to_reg = {rec.get("document_id"): rec for rec in registry.values()}

legacy = []
current = []
for name in sorted(utility.list_collections()):
    if not name.startswith("rag_"):
        continue
    try:
        c = Collection(name)
        n = c.num_entities
        if n == 0:
            print(f"  [EMPTY] {name}")
            continue
        res = c.query(expr="chunk_index >= 0", limit=1, output_fields=["metadata"])
        md = (res[0].get("metadata") or {}) if res else {}
        has_profile = bool(md.get("chunk_profile"))
        doc_id = None
        # 从注册表反查 doc_id（collection 名含 12 位哈希）
        for did, rec in id_to_reg.items():
            if rec.get("collection_name") == name:
                doc_id = did
                break
        row = (name, n, doc_id)
        (current if has_profile else legacy).append(row)
        mark = "CURRENT" if has_profile else "LEGACY"
        print(f"  [{mark:>7}] {name} entities={n} doc={doc_id}")
    except Exception as exc:
        print(f"  [ERR] {name}: {exc}")

print(f"\n== legacy（需重建）: {len(legacy)} 个 collection ==")
for name, n, doc_id in legacy:
    print(f"  {name} entities={n} doc={doc_id}")
