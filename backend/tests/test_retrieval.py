# -*- coding: utf-8 -*-
"""检索与分块的纯函数行为：分块父子关系、关键词抽取、相关性过滤、BM25 打分。"""
from __future__ import annotations

from app.parsers import parse_frontmatter, split_text
from app.rag import _query_terms, _relevance_ok
from app.vector_store import BM25Index, _tokenize


def test_split_text_produces_child_chunks_with_parent():
    text = "# 对数函数\n\n" + ("对数函数的定义域是 x>0。" * 40) + "\n\n# 矩阵\n\n" + ("矩阵的秩。" * 40)
    chunks = split_text(text, chunk_size=120, overlap=20)

    assert chunks
    assert all({"idx", "content"} <= set(c) for c in chunks)
    assert any(c.get("parent_content") for c in chunks)
    short = [c for c in chunks if len(c["content"]) > 120 * 2]
    assert not short, "子块长度应受 chunk_size 约束"


def test_split_text_filters_noise_and_keeps_real_content():
    assert split_text("", 100, 10) == []
    # 少于 20 字的内容按"过短噪声"丢弃（parsers.split_text 末尾的既有规则）
    assert split_text("短短一句。", 100, 10) == []
    one = split_text("对数函数的定义域是 x>0，底数大于 0 且不等于 1，这是基本性质。", 100, 10)
    assert len(one) == 1 and one[0]["content"].strip()


def test_query_terms_keeps_core_concept_word():
    terms = _query_terms("什么是对数函数？")
    assert "对数" in terms or "对数函数" in terms


def test_relevance_filter_drops_unrelated_noise():
    hit = {"content": "对数函数的定义域是 x>0，底数大于 0 且不等于 1。", "doc_name": "10_概念·对数函数", "section": ""}
    assert _relevance_ok("今天天气怎么样", hit, top_score=0.9, mode="hybrid") is False
    assert _relevance_ok("什么是对数函数", hit, top_score=0.9, mode="hybrid") is True


def test_bm25_ranks_matching_doc_first():
    idx = BM25Index(k1=1.5, b=0.75)
    idx.build([
        {"content": "对数函数 定义域 底数", "section": "", "doc_name": "概念·对数函数"},
        {"content": "矩阵 秩 行列式", "section": "", "doc_name": "概念·矩阵"},
    ])
    scores = idx.score(_tokenize("对数函数"))
    assert scores[0] > scores[1]


def test_frontmatter_parses_fields():
    fm, body = parse_frontmatter("---\ntitle: 对数函数\npart: 函数\n---\n正文内容")
    assert fm.get("title") == "对数函数"
    assert fm.get("part") == "函数"
    assert body.strip() == "正文内容"
