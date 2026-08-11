"""本地复现 parse → profile → chunk，看每个 block 是否都产出 chunk。"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.rag.parsers.markdown_parser import MarkdownParser
from backend.app.rag.chunking_profiles import resolve_profile, group_blocks_for_profile
from backend.app.rag.chunkers import get_chunker

SRC = PROJECT_ROOT / "data/uploads/doc_3f570a771fd1.md"


def main():
    blocks = MarkdownParser("doc_3f570a771fd1").parse(SRC)
    print(f"== parser 产出 {len(blocks)} blocks ==")
    for i, b in enumerate(blocks):
        print(f"  block[{i}] type={b.content_type} hp={' > '.join(b.heading_path)} len={len(b.text or '')}")
        if b.content_type == "heading":
            print(f"           text={b.text!r}")

    profile = resolve_profile("auto", SRC.name, blocks)
    print(f"\n== profile = {profile.id} chunker={profile.chunker} params={profile.params} ==")

    grouped = group_blocks_for_profile(blocks, profile)
    print(f"== group_blocks_for_profile 后 {len(grouped)} blocks ==")

    chunker = get_chunker(profile.chunker, **profile.params)
    total = 0
    dropped = []
    for i, b in enumerate(grouped):
        text = b.text
        if not text or not text.strip():
            continue
        chunks = chunker.chunk(text)
        if not chunks:
            chunks = [text]
        for c in chunks:
            if c.strip():
                total += 1
        if not chunks:
            dropped.append((i, b.heading_path, len(text)))
    print(f"\n== 理论上应产出 chunk 数: {total} ==")
    print(f"== 无 chunk 的 block: {dropped} ==")


if __name__ == "__main__":
    main()
