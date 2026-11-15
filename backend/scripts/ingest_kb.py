# -*- coding: utf-8 -*-
"""命令行批量入库数学知识库。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import CHUNK_OVERLAP, CHUNK_SIZE, KB_SOURCE_DIR  # noqa: E402
from app.db import get_conn, init_db, now  # noqa: E402
from app.parsers import parse_document, parse_frontmatter, split_text  # noqa: E402
from app.vector_store import STORE  # noqa: E402


def ingest(root: Path, replace: bool = False) -> dict:
    init_db()
    imported = skipped = 0
    errors = []
    files = [p for p in root.rglob("*") if p.suffix.lower() in {".md", ".txt", ".docx", ".pdf"}]
    for fp in files:
        try:
            text, meta = parse_document(fp)
            fm, body = parse_frontmatter(text)
            book = str(fm.get("book", "") or "")
            part = str(fm.get("part", "") or "")
            pages = str(fm.get("pages", "") or "")
            name = str(fm.get("title", "") or fp.stem)
            with get_conn() as conn:
                exists = conn.execute("SELECT id FROM documents WHERE name=?", (name,)).fetchone()
                if exists:
                    if not replace:
                        skipped += 1
                        continue
                    # --replace：用新解析结果（含父子分块）重建该文档
                    doc_id = exists["id"]
                    conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
                    conn.execute(
                        "UPDATE documents SET path=?,format=?,size=?,chunk_count=?,book=?,part=?,pages=?,updated_at=? WHERE id=?",
                        (str(fp), meta.get("format", fp.suffix.lstrip(".")), fp.stat().st_size,
                         0, book, part, pages, now(), doc_id),
                    )
                else:
                    doc_id = None
                chunks = split_text(body, CHUNK_SIZE, CHUNK_OVERLAP)
                if doc_id is None:
                    cur = conn.execute(
                        """INSERT INTO documents(name,path,format,size,status,chunk_count,book,part,pages,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (name, str(fp), meta.get("format", fp.suffix.lstrip(".")), fp.stat().st_size,
                         "ready", len(chunks), book, part, pages, now(), now()),
                    )
                    doc_id = cur.lastrowid
                else:
                    conn.execute("UPDATE documents SET chunk_count=?, status='ready', updated_at=? WHERE id=?", (len(chunks), now(), doc_id))
                for c in chunks:
                    conn.execute(
                        "INSERT INTO chunks(doc_id,content,section,page_hint,idx,vector_id,parent_content,parent_idx) VALUES(?,?,?,?,?,?,?,?)",
                        (doc_id, c["content"], c.get("section", ""), pages, c["idx"], f"{doc_id}-{c['idx']}",
                         c.get("parent_content", ""), c.get("parent_idx", -1)),
                    )
            if replace:
                STORE.delete_by_doc(doc_id)
            STORE.add([
                {
                    "content": c["content"],
                    "doc_id": doc_id,
                    "doc_name": name,
                    "section": c.get("section", ""),
                    "page_hint": pages,
                    "part": part,
                    "book": book,
                    "idx": c["idx"],
                    "parent_idx": c.get("parent_idx"),
                    "parent_content": c.get("parent_content", ""),
                }
                for c in chunks
            ])
            imported += 1
            print(f"[OK] {name} chunks={len(chunks)}")
        except Exception as e:
            errors.append(f"{fp.name}: {e}")
            print(f"[ERR] {fp.name}: {e}")
    return {"imported": imported, "skipped": skipped, "errors": errors}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=str(KB_SOURCE_DIR))
    ap.add_argument("--replace", action="store_true", help="已存在文档用新解析结果重建（父子分块后全量重建用）")
    args = ap.parse_args()
    print("INGEST", args.path, "replace=", args.replace)
    result = ingest(Path(args.path), replace=args.replace)
    print("RESULT", result)
    print("VECTOR", STORE.stats())
