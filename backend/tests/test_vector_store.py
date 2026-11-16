# -*- coding: utf-8 -*-
"""向量库：RRF 排序、空索引、维度不匹配自愈、索引损坏时按数据库重建（P0-5 回归）。"""
from __future__ import annotations

import json

import numpy as np

from app import db as dbm
from app import vector_store as vs


def _hit(doc_id: int, idx: int, score: float) -> dict:
    return {"doc_id": doc_id, "idx": idx, "content": f"片段{doc_id}-{idx}", "score": score, "doc_name": "文档"}


def test_rrf_prefers_item_in_both_lists(store):
    vhits = [_hit(1, 0, 0.9), _hit(2, 0, 0.8)]
    bhits = [_hit(1, 0, 12.0), _hit(3, 0, 9.0)]
    merged = store._rrf_merge(vhits, bhits, top_k=3)
    assert len(merged) == 3
    assert (merged[0]["doc_id"], merged[0]["idx"]) == (1, 0)  # 两路都命中应排第一


def test_search_on_empty_index_returns_empty(store):
    assert store._vector_search("对数函数", 5) == []
    assert store._bm25_search("对数函数", 5) == []


def test_add_then_reload_stays_consistent(store):
    store.add(
        [{"content": f"测试片段{i}", "doc_id": 1, "doc_name": "概念卡", "section": "", "page_hint": ""}
         for i in range(5)]
    )
    assert store.vectors.shape[0] == len(store.meta) == 5

    fresh = vs.VectorStore()
    assert fresh.vectors.shape[0] == len(fresh.meta) == 5


def test_concurrent_add_keeps_meta_and_vectors_in_sync(store):
    import threading

    items = [
        {"content": f"并发片段{i}", "doc_id": i % 3, "doc_name": f"卡{i}", "section": "", "page_hint": ""}
        for i in range(24)
    ]
    threads = [threading.Thread(target=store.add, args=(items[i * 4 : (i + 1) * 4],)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(store.meta) == store.vectors.shape[0] == 24


def test_dimension_mismatch_triggers_rebuild(store):
    store.add([{"content": "对数函数的定义域与性质", "doc_id": 1, "doc_name": "概念·对数函数", "section": "", "page_hint": ""}])
    store.vectors = np.zeros((len(store.meta), 128), dtype=np.float32)  # 维度被破坏

    hits = store._vector_search("对数函数", 3)

    assert store.vectors.shape[1] == store.embedder.dim
    assert hits and hits[0]["doc_id"] == 1


def test_corrupt_index_rebuilds_from_db(store, temp_db, tmp_path):
    with dbm.get_conn() as conn:
        conn.execute(
            "INSERT INTO documents(id,name,path,format,size,status,chunk_count,book,part,pages,created_at,updated_at) "
            "VALUES(2,'概念·对数函数','p.md','md',10,'ready',2,'','','',0,0)"
        )
        for i in range(2):
            conn.execute(
                "INSERT INTO chunks(doc_id,content,section,page_hint,idx,vector_id,parent_content,parent_idx) "
                "VALUES(2,?,'','',?,'',?, -1)",
                (f"正文{i}", i, ""),
            )

    (tmp_path / "index.json").write_text(json.dumps([{"doc_id": 2, "idx": 0, "content": "x"}]), encoding="utf-8")
    np.save(tmp_path / "vectors.npy", np.zeros((3, 512), dtype=np.float32))  # meta=1 与 vectors=3 不一致

    fresh = vs.VectorStore()

    assert len(fresh.meta) == 2 and fresh.vectors.shape == (2, fresh.embedder.dim)
    assert {m["content"] for m in fresh.meta} == {"正文0", "正文1"}


def test_atomic_save_leaves_no_temp_files(store, tmp_path):
    store.add([{"content": "写入一次", "doc_id": 1, "doc_name": "卡", "section": "", "page_hint": ""}])
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["index.json", "vectors.npy"]
