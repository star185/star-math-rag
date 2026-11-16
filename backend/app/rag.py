# -*- coding: utf-8 -*-
"""RAG 编排：检索、组装、生成。"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Iterator

from .config import RETRIEVAL_MODE, TOP_K
from .llm import build_messages, llm_configured, stream_chat_completion
from .vector_store import STORE

logger = logging.getLogger(__name__)


import re


def _query_terms(question: str) -> list[str]:
    """提取中文 2–6 字关键词：去符号/疑问词后滑窗，按长度分组保留长短词。
    （与 vector_store._query_terms_local 一致；分组保留保证「分布」「矩阵」等 2 字核心词不被长词挤掉）"""
    compact = re.sub(r"\s+", "", question or "")
    compact = re.sub(r"[「」『』,，。、；;：:！!？?()（）\[\]【】\"'《》<>|*#\-—…]+", "", compact)
    # 去掉常见疑问词
    for w in ("什么是", "如何理解", "简述", "请说明", "有哪些", "为什么", "怎么", "吗？", "吗", "？", "?", "。"):
        compact = compact.replace(w, "")
    terms = set()
    # 整串（若较短）
    if 2 <= len(compact) <= 12:
        terms.add(compact)
    # 滑窗 2–6 字
    for n in (6, 5, 4, 3, 2):
        for i in range(max(0, len(compact) - n + 1)):
            t = compact[i : i + n]
            if re.fullmatch(r"[一-鿿]+", t):
                terms.add(t)
    longs = sorted((t for t in terms if len(t) >= 4), key=lambda x: -len(x))
    mids = sorted((t for t in terms if len(t) == 3), key=lambda x: -len(x))
    shorts = sorted((t for t in terms if len(t) == 2), key=lambda x: -len(x))
    return longs + mids + shorts


def _relevance_ok(question: str, h: dict, top_score: float, mode: str = "vector") -> bool:
    """过滤弱相关片段。

    - vector：分数阈值 + 关键词命中（原逻辑）；
    - hybrid / keyword：BM25/RRF 分数不可与余弦直接比较，改用关键词命中过滤。
    """
    content = (h.get("content") or "") + " " + (h.get("section") or "") + " " + (h.get("doc_name") or "")
    content = re.sub(r"\s+", "", content)
    terms = _query_terms(question)
    if terms:
        hits = [t for t in terms if t in content]
        if not hits:
            return False
        # 优先要求有较长词命中（避免「两个」误伤「两个答案」）
        long_hit = any(len(t) >= 3 for t in hits)
        if not long_hit:
            # 仅 2 字命中时，要求内容里也有同段强相关标题
            hay_head = (h.get("section") or "") + (h.get("doc_name") or "")
            return any(t in hay_head for t in hits)
    elif mode != "vector":
        return True

    if mode != "vector":
        return True
    score = float(h.get("score") or 0)
    if top_score <= 0:
        return score >= 0.15
    # 相对阈值：低于最高分的 55% 且绝对分不高则丢弃
    if score < top_score * 0.55 and score < 0.32:
        return False
    if score < 0.12:
        return False
    return True


def retrieve(
    question: str,
    top_k: int = TOP_K,
    part: str | None = None,
    doc_id: int | None = None,
    mode: str | None = None,
) -> list[dict]:
    mode = (mode or RETRIEVAL_MODE or "hybrid").lower()
    # 无有效中文内容的查询直接判空：拦截乱码/纯英文/空白检索产生的噪声命中
    if not re.search(r"[一-鿿]", question or ""):
        return []
    # 多取一些再过滤
    raw_k = max(top_k * 3, 12)
    hits = STORE.search(question, top_k=raw_k, part=part, doc_id=doc_id, mode=mode)
    if not hits:
        return []
    # F7 反例联动：反例意图查询 → 提高「反例」卡片权重（数学语境下反例是答案核心）
    counter_words = ("反例", "例子", "不成立", "失效", "例外", "什么时候不能", "边界情况", "适用条件", "举一个")
    if any(w in (question or "") for w in counter_words):
        for h in hits:
            if "反例" in (h.get("doc_name") or ""):
                h["score"] = float(h.get("score") or 0) + (8.0 if mode != "vector" else 0.15)
        hits.sort(key=lambda h: float(h.get("score") or 0), reverse=True)
    top_score = float(hits[0].get("score") or 0)

    filtered = [h for h in hits if _relevance_ok(question, h, top_score, mode)]
    # 若过滤过狠，至少保留 Top-1
    if not filtered:
        filtered = hits[:1]
    # 去掉低质量噪声块（目录/纯图片引用 / 汉字极少）
    cleaned = []
    for h in filtered:
        full = h.get("content") or ""
        doc_name = h.get("doc_name") or ""
        if doc_name == "00_目录" or doc_name.startswith("00_"):
            continue  # 目录/章节框架块不是知识内容，不作为检索命中返回
        text_only = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", full).strip()
        if not text_only:
            continue  # 整块只有图片引用，无可读文字
        han = len(re.findall(r"[一-鿿]", text_only))
        # 标题精确命中的概念卡（正文以 LaTeX 公式为主、汉字少）豁免汉字数量过滤
        doc_card = re.sub(r"^\d+_[^·]+·", "", doc_name)
        exact_hit = any(t and t == doc_card for t in _query_terms(question))
        if han < 15 and (len(text_only) > 40 or han == 0) and not exact_hit:
            continue
        cleaned.append(h)
    if not cleaned:
        # 全部为图片/无文字块时不再回退，避免把噪声当命中返回
        return []
    filtered = cleaned[:top_k]

    sources = []
    for i, h in enumerate(filtered, 1):
        full = h.get("content") or ""
        sources.append(
            {
                "id": i,
                "doc_id": h.get("doc_id"),
                "doc_name": h.get("doc_name") or h.get("title"),
                "section": h.get("section", ""),
                "page_hint": h.get("page_hint") or h.get("pages", ""),
                "score": round(float(h.get("score", 0)), 4),
                "vec_score": h.get("vec_score"),
                "bm25_score": h.get("bm25_score"),
                "parent_idx": h.get("parent_idx"),
                "parent_content": h.get("parent_content") or "",
                "content": full,
                "snippet": full[:240],
            }
        )
    return sources


def answer_stream(
    question: str,
    history: list[dict] | None = None,
    top_k: int = TOP_K,
    part: str | None = None,
    mode: str = "rag",
    retrieval_mode: str | None = None,
) -> Iterator[dict]:
    """
    Yields events:
      {"type":"sources","data":[...]}
      {"type":"token","data":"..."}
      {"type":"meta","data":{...}}
    """
    t0 = time.time()
    if mode == "baseline":
        contexts = []
    else:
        contexts = retrieve(question, top_k=top_k, part=part, mode=retrieval_mode)
    yield {"type": "sources", "data": contexts}

    messages = build_messages(question, contexts, history, mode=mode)
    buf = []
    truncated = False
    try:
        for token in stream_chat_completion(messages):
            if isinstance(token, tuple):  # ("__truncated__", True)：输出被长度截断
                truncated = True
                continue
            buf.append(token)
            yield {"type": "token", "data": token}
    except Exception as e:
        logger.exception("问答生成失败: %s", e)
        err = "生成出错，请稍后重试"
        yield {"type": "token", "data": err}
        buf.append(err)

    latency = int((time.time() - t0) * 1000)
    yield {
        "type": "meta",
        "data": {
            "latency_ms": latency,
            "mode": mode,
            "llm": llm_configured(),
            "top_k": top_k,
            "source_count": len(contexts),
            "truncated": truncated,
        },
    }


def answer_complete(question: str, history: list[dict] | None = None, top_k: int = TOP_K, mode: str = "rag") -> dict[str, Any]:
    sources: list = []
    tokens: list[str] = []
    meta: dict = {}
    for ev in answer_stream(question, history=history, top_k=top_k, mode=mode):
        if ev["type"] == "sources":
            sources = ev["data"]
        elif ev["type"] == "token":
            tokens.append(ev["data"])
        elif ev["type"] == "meta":
            meta = ev["data"]
    return {
        "answer": "".join(tokens),
        "sources": sources,
        "meta": meta,
    }
