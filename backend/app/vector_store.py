# -*- coding: utf-8 -*-
"""本地向量库：字符 n-gram + 余弦相似度（可选远程 Embedding）+ BM25 关键词检索 + RRF 融合。

借鉴 RAGFlow 混合检索思路：向量（语义）与 BM25（关键词精确匹配）双路召回，
再用 RRF（Reciprocal Rank Fusion）融合排序，兼顾召回率与精确率。
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import zlib
from pathlib import Path

import numpy as np

from .config import (
    BM25_B,
    BM25_K1,
    EMBED_API_BASE,
    EMBED_API_KEY,
    EMBED_MODEL,
    RETRIEVAL_MODE,
    RRF_K,
    VECTOR_DIR,
)


def stable_hash(text: str) -> int:
    return zlib.crc32(text.encode("utf-8")) & 0x7FFFFFFF


INDEX_PATH = VECTOR_DIR / "index.json"
VEC_PATH = VECTOR_DIR / "vectors.npy"
VOCAB_PATH = VECTOR_DIR / "vocab.json"

logger = logging.getLogger(__name__)


def _ngrams(text: str, n: int = 2) -> list[str]:
    s = re.sub(r"\s+", "", text.lower())
    if len(s) <= n:
        return [s] if s else []
    return [s[i : i + n] for i in range(len(s) - n + 1)]


def _tokenize(text: str) -> list[str]:
    """BM25 分词：优先 jieba；不可用时回退到字符 unigram+bigram。"""
    s = (text or "").lower()
    try:
        import jieba

        return [t for t in jieba.lcut(s) if t.strip() and re.search(r"[\u4e00-\u9fff0-9a-z]", t)]
    except Exception:
        pass
    s2 = re.sub(r"\s+", "", s)
    toks = [ch for ch in s2 if re.search(r"[\u4e00-\u9fff0-9a-z]", ch)]
    for i in range(len(s2) - 1):
        g = s2[i : i + 2]
        if re.search(r"[\u4e00-\u9fff]", g):
            toks.append(g)
    return toks


class BM25Index:
    """轻量 BM25 全文索引（纯内存，零外部服务）。"""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs: list[list[str]] = []
        self.dl: list[int] = []
        self.avgdl: float = 0.0
        self.idf: dict[str, float] = {}
        self.built = False

    def build(self, metas: list[dict]) -> None:
        self.docs, self.dl = [], []
        df: dict[str, int] = {}
        for m in metas:
            text = " ".join(
                str(x) for x in (m.get("content", ""), m.get("section", ""), m.get("doc_name", "")) if x
            )
            toks = _tokenize(text)
            self.docs.append(toks)
            self.dl.append(len(toks))
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        n = len(self.docs)
        self.avgdl = (sum(self.dl) / n) if n else 0.0
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.built = True

    def score(self, query_tokens: list[str]) -> list[float]:
        scores = [0.0] * len(self.docs)
        for t in set(query_tokens):
            idf = self.idf.get(t, 0.0)
            if idf <= 0 or not self.docs:
                continue
            for i, dl in enumerate(self.dl):
                if not dl:
                    continue
                tf = self.docs[i].count(t)
                if tf:
                    denom = tf + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                    scores[i] += idf * (tf * (self.k1 + 1)) / denom
        return scores


class LocalEmbedder:
    """中文友好哈希向量：1/2/3-gram + 关键词加权，离线可用。"""

    def __init__(self, dim: int = 512):
        self.dim = dim

    def _tokens(self, text: str) -> list[tuple[str, float]]:
        s = re.sub(r"[\s\|\*\#\-—…、，。；：？！「」『』（）【】\.,;:?!]+", "", text.lower())
        toks: list[tuple[str, float]] = []
        # 单字（弱）
        for ch in s:
            if "一" <= ch <= "鿿" or ch.isalnum():
                toks.append((ch, 0.25))
        # 2/3 字词（强）
        for n, w in ((2, 1.0), (3, 1.25)):
            for i in range(len(s) - n + 1):
                g = s[i : i + n]
                if re.search(r"[一-鿿]", g):
                    toks.append((g, w))
        # 4 字成语/短语（更强）
        for i in range(len(s) - 3):
            g = s[i : i + 4]
            if re.fullmatch(r"[一-鿿]{4}", g):
                toks.append((g, 1.4))
        return toks

    def encode(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        for tok, w in self._tokens(text):
            h1 = stable_hash(tok) % self.dim
            h2 = stable_hash(tok + "#2") % self.dim
            vec[h1] += w
            vec[h2] += w * 0.5
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec /= norm
        return vec

    def encode_batch(self, texts: list[str]) -> np.ndarray:
        return np.vstack([self.encode(t) for t in texts]) if texts else np.zeros((0, self.dim), dtype=np.float32)


class RemoteEmbedder:
    def __init__(self, api_base: str, api_key: str, model: str):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dim = 1536

    def encode(self, text: str) -> np.ndarray:
        return self.encode_batch([text])[0]

    def encode_batch(self, texts: list[str]) -> np.ndarray:
        import urllib.error
        import urllib.request

        url = f"{self.api_base}/embeddings"
        body = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        rows = [item["embedding"] for item in data.get("data", [])]
        arr = np.array(rows, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return arr / norms


def get_embedder():
    if EMBED_API_BASE and EMBED_API_KEY:
        try:
            return RemoteEmbedder(EMBED_API_BASE, EMBED_API_KEY, EMBED_MODEL)
        except Exception:
            pass
    return LocalEmbedder()


class VectorStore:
    def __init__(self):
        # 全部写操作与状态读取经这把锁；读路径只在锁内取快照，计算在锁外进行
        self._lock = threading.RLock()
        self.embedder = get_embedder()
        self.meta: list[dict] = []
        self.vectors: np.ndarray = np.zeros((0, getattr(self.embedder, "dim", 384)), dtype=np.float32)
        self.bm25 = BM25Index(k1=BM25_K1, b=BM25_B)
        self._bm25_dirty = True
        self.load()

    # ---------- persistence ----------
    def _empty_vectors(self) -> np.ndarray:
        return np.zeros((0, getattr(self.embedder, "dim", 384)), dtype=np.float32)

    def load(self) -> None:
        """加载索引。文件缺失/损坏/两份不一致时，以数据库为准重建。"""
        with self._lock:
            if not (INDEX_PATH.exists() and VEC_PATH.exists()):
                self.meta, self.vectors = [], self._empty_vectors()
                self._bm25_dirty = True
                return
            try:
                meta = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
                vectors = np.load(VEC_PATH)
                if not isinstance(meta, list) or vectors.ndim != 2 or len(meta) != vectors.shape[0]:
                    raise ValueError(
                        f"索引与向量不一致：meta={len(meta) if isinstance(meta, list) else type(meta).__name__} "
                        f"vectors={getattr(vectors, 'shape', None)}"
                    )
                self.meta = meta
                self.vectors = np.asarray(vectors, dtype=np.float32)
                self._bm25_dirty = True
            except Exception as e:
                logger.warning("向量库加载失败，改为按数据库重建：%s", e)
                self.meta, self.vectors = [], self._empty_vectors()
                self._bm25_dirty = True
                self.rebuild_from_db()

    def save(self) -> None:
        """两个文件先写临时文件再原子替换，避免并发写留下半份索引。"""
        with self._lock:
            VECTOR_DIR.mkdir(parents=True, exist_ok=True)
            idx_tmp = INDEX_PATH.with_name(INDEX_PATH.name + ".tmp")
            vec_tmp = VEC_PATH.with_name(VEC_PATH.name + ".tmp")
            with open(idx_tmp, "w", encoding="utf-8") as f:
                f.write(json.dumps(self.meta, ensure_ascii=False))
                f.flush()
                os.fsync(f.fileno())
            with open(vec_tmp, "wb") as f:
                np.save(f, self.vectors)  # 传句柄，避免 np.save 自行拼接 .npy 后缀
                f.flush()
                os.fsync(f.fileno())
            os.replace(idx_tmp, INDEX_PATH)
            os.replace(vec_tmp, VEC_PATH)

    def rebuild_from_db(self) -> int:
        """以 documents/chunks 为准重建索引与向量（索引文件损坏时的兜底路径）。"""
        from .db import get_conn

        with self._lock:
            try:
                with get_conn() as conn:
                    rows = conn.execute(
                        "SELECT c.*, d.name AS doc_name, d.part AS part, d.book AS book "
                        "FROM chunks c JOIN documents d ON d.id = c.doc_id ORDER BY c.doc_id, c.idx"
                    ).fetchall()
            except Exception as e:
                logger.error("按数据库重建向量库失败：%s", e)
                return 0
            items = []
            for r in rows:
                keys = r.keys()
                items.append(
                    {
                        "content": r["content"] or "",
                        "doc_id": r["doc_id"],
                        "doc_name": r["doc_name"] or "",
                        "section": r["section"] or "",
                        "page_hint": r["page_hint"] or "",
                        "part": r["part"] or "",
                        "book": r["book"] or "",
                        "idx": r["idx"],
                        "parent_content": (r["parent_content"] or "") if "parent_content" in keys else "",
                        "parent_idx": (r["parent_idx"] if "parent_idx" in keys else -1) or -1,
                    }
                )
            texts = [it["content"] for it in items]
            self.meta = items
            self.vectors = self.embedder.encode_batch(texts) if items else self._empty_vectors()
            self._bm25_dirty = True
            self.save()
            logger.info("向量库已按数据库重建：%d 片段", len(items))
            return len(items)

    def clear(self) -> None:
        with self._lock:
            self.meta = []
            self.vectors = self._empty_vectors()
            self._bm25_dirty = True
            self.save()

    def add(self, items: list[dict]) -> int:
        """items: [{content, doc_id, doc_name, section, page_hint, parent_content?, parent_idx?, ...}]"""
        if not items:
            return 0
        with self._lock:
            texts = [it["content"] for it in items]
            vecs = self.embedder.encode_batch(texts)
            self.vectors = vecs if self.vectors.size == 0 else np.vstack([self.vectors, vecs])
            # 整体替换列表而非原地 append：读路径持有的旧快照不会看到长度突变
            self.meta = self.meta + list(items)
            self._bm25_dirty = True
            self.save()
            return len(items)

    def delete_by_doc(self, doc_id: int) -> int:
        with self._lock:
            if not self.meta:
                return 0
            keep_idx = [i for i, m in enumerate(self.meta) if int(m.get("doc_id", -1)) != int(doc_id)]
            removed = len(self.meta) - len(keep_idx)
            self.meta = [self.meta[i] for i in keep_idx]
            self.vectors = self.vectors[keep_idx] if keep_idx else self._empty_vectors()
            self._bm25_dirty = True
            self.save()
            return removed

    # ---------- BM25 ----------
    def _ensure_bm25(self) -> None:
        # 重建到新对象再整体替换：并发查询手里持有的旧索引不会被就地改写
        with self._lock:
            if self._bm25_dirty:
                idx = BM25Index(k1=BM25_K1, b=BM25_B)
                idx.build(self.meta)
                self.bm25 = idx
                self._bm25_dirty = False

    def _bm25_search(self, query: str, top_k: int, part: str | None = None, doc_id: int | None = None) -> list[dict]:
        self._ensure_bm25()
        with self._lock:
            bm25 = self.bm25
            meta = self.meta
        toks = _tokenize(query)
        if not toks or not bm25.built:
            return []
        scores = bm25.score(toks)
        # 标题加权：卡片正文以 LaTeX 为主、术语集中在标题。
        # 概念卡 BM25 底分往往低于中文密集的反例集/地图卡，此处加权保证概念卡进入融合候选。
        q_terms = self._query_terms_local(query)
        exact_idx: set[int] = set()
        if q_terms:
            for i, m in enumerate(meta):
                doc_name = m.get("doc_name") or ""
                card = re.sub(r"^\d+_[^·]+·", "", doc_name)
                boost = 0.0
                for t in q_terms:
                    if not t:
                        continue
                    if card and t == card:
                        boost = max(boost, 10.0 if len(t) >= 3 else 5.0)
                    elif card and len(t) >= 2 and t in card:
                        boost = max(boost, 2.0)
                if boost:
                    scores[i] += boost
                if card and any(t and t == card for t in q_terms):
                    exact_idx.add(i)
        # 标题精确命中卡强制优先（数学术语集中在标题，是强信号，不参与词频竞争）
        order = sorted(range(len(scores)), key=lambda i: (0 if i in exact_idx else 1, -scores[i]))
        out = []
        for i in order:
            if scores[i] <= 0:
                break
            m = meta[int(i)]
            if part and m.get("part") != part:
                continue
            if doc_id is not None and int(m.get("doc_id", -1)) != int(doc_id):
                continue
            out.append({**m, "score": float(scores[i])})
            if len(out) >= top_k:
                break
        return out

    # ---------- vector ----------
    def _vector_search(self, query: str, top_k: int, part: str | None = None, doc_id: int | None = None) -> list[dict]:
        with self._lock:
            meta = self.meta
            vectors = self.vectors
        if not meta:
            return []
        q = self.embedder.encode(query)
        if vectors.shape[1] != q.shape[0]:
            self.rebuild()
            with self._lock:
                meta = self.meta
                vectors = self.vectors
            if vectors.shape[1] != q.shape[0]:
                return []
        scores = np.asarray(vectors @ q, dtype=np.float32).reshape(-1)

        # 标题/章节加权：查询关键词命中 meta 字段则加分
        q_terms = self._query_terms_local(query)
        for i, m in enumerate(meta):
            hay = f"{m.get('doc_name','')} {m.get('section','')} {m.get('title','')}"
            bonus = 0.0
            for t in q_terms:
                if t and t in hay:
                    bonus += 0.35
            # 整句短语命中
            q_compact = re.sub(r"\s+", "", query)
            if len(q_compact) >= 4 and q_compact in re.sub(r"\s+", "", m.get("content", "")):
                bonus += 0.12
            scores[i] += bonus

        order = np.argsort(-scores)
        out = []
        for i in order:
            m = meta[int(i)]
            if part and m.get("part") != part:
                continue
            if doc_id is not None and int(m.get("doc_id", -1)) != int(doc_id):
                continue
            out.append({**m, "score": float(scores[int(i)])})
            if len(out) >= top_k:
                break
        return out

    # ---------- hybrid ----------
    def _rrf_merge(self, vhits: list[dict], bhits: list[dict], top_k: int) -> list[dict]:
        """RRF 融合：按排名位置打分，避免不同检索引擎分数不可比的问题。"""
        merged: dict[tuple, dict] = {}
        for i, h in enumerate(vhits, 1):
            key = (int(h.get("doc_id", -1)), int(h.get("idx", -1)))
            e = merged.setdefault(key, {"item": h, "rrf": 0.0, "vec_score": None, "bm25_score": None, "first": i})
            e["rrf"] += 1.0 / (RRF_K + i)
            e["vec_score"] = float(h.get("score", 0.0))
        for i, h in enumerate(bhits, 1):
            key = (int(h.get("doc_id", -1)), int(h.get("idx", -1)))
            e = merged.setdefault(key, {"item": h, "rrf": 0.0, "vec_score": None, "bm25_score": None, "first": i})
            e["rrf"] += 1.0 / (RRF_K + i)
            e["bm25_score"] = float(h.get("score", 0.0))
        ordered = sorted(merged.values(), key=lambda e: (-e["rrf"], e["first"]))
        out = []
        for e in ordered[:top_k]:
            item = dict(e["item"])
            item["score"] = round(e["rrf"], 6)
            item["vec_score"] = e["vec_score"]
            item["bm25_score"] = e["bm25_score"]
            item["mode"] = "hybrid"
            out.append(item)
        return out

    # ---------- 标题加权（数学语料优化） ----------
    @staticmethod
    def _query_terms_local(question: str) -> list[str]:
        """提取中文 2–6 字关键词：去符号/疑问词后滑窗，按长度分组保留长短词。
        （与 rag._query_terms 同逻辑，避免循环依赖；分组保留保证「分布」「矩阵」等 2 字核心词不被长词挤掉）"""
        compact = re.sub(r"\s+", "", question or "")
        compact = re.sub(r"[「」『』,，。、；;：:！!？?()（）\[\]【】\"'《》<>|*#\-—…]+", "", compact)
        for w in ("什么是", "如何理解", "简述", "请说明", "有哪些", "为什么", "怎么", "吗？", "吗", "？", "?", "。"):
            compact = compact.replace(w, "")
        terms = set()
        if 2 <= len(compact) <= 12:
            terms.add(compact)
        for n in (6, 5, 4, 3, 2):
            for i in range(max(0, len(compact) - n + 1)):
                t = compact[i : i + n]
                if re.fullmatch(r"[一-鿿]+", t):
                    terms.add(t)
        longs = sorted((t for t in terms if len(t) >= 4), key=lambda x: -len(x))
        mids = sorted((t for t in terms if len(t) == 3), key=lambda x: -len(x))
        shorts = sorted((t for t in terms if len(t) == 2), key=lambda x: -len(x))
        return longs + mids + shorts

    def _boost_title(self, hits: list[dict], query: str, mode: str) -> list[dict]:
        """卡片正文以 LaTeX 公式为主、中文术语集中在标题（doc_name）。
        对「标题精确命中 / 包含查询术语」的片段加权，解决纯概念名查询召回不足。"""
        terms = self._query_terms_local(query)
        if not terms:
            return hits
        for h in hits:
            doc_name = h.get("doc_name") or h.get("title") or ""
            card = re.sub(r"^\d+_[^·]+·", "", doc_name)
            boost = 0.0
            for t in terms:
                if not t:
                    continue
                if card and t == card:
                    boost = max(boost, 8.0 if len(t) >= 3 else 4.0) if mode != "vector" else max(boost, 0.35 if len(t) >= 3 else 0.2)
                elif card and len(t) >= 2 and t in card:
                    boost = max(boost, 1.5 if mode != "vector" else 0.05)
            if boost:
                h["score"] = float(h.get("score") or 0) + boost
        hits.sort(key=lambda h: float(h.get("score") or 0), reverse=True)
        return hits

    def search(
        self,
        query: str,
        top_k: int = 5,
        part: str | None = None,
        doc_id: int | None = None,
        mode: str | None = None,
    ) -> list[dict]:
        """检索入口。mode: hybrid（默认）/ vector / keyword。
        统一做标题加权（_boost_title），并对 hybrid 扩大融合候选，保证标题命中卡不被挤出。"""
        if not self.meta:
            return []
        mode = (mode or RETRIEVAL_MODE or "hybrid").lower()
        raw_k = max(top_k * 4, 24)
        if mode == "keyword":
            hits = self._bm25_search(query, raw_k, part, doc_id)
            return self._boost_title(hits, query, mode)[:top_k]
        if mode == "vector":
            hits = self._vector_search(query, raw_k, part, doc_id)
            return self._boost_title(hits, query, mode)[:top_k]
        vhits = self._vector_search(query, raw_k, part, doc_id)
        bhits = self._bm25_search(query, raw_k, part, doc_id)
        merged = self._rrf_merge(vhits, bhits, max(top_k * 2, 12))
        return self._boost_title(merged, query, mode)[:top_k]

    def rebuild(self) -> None:
        """用现有 meta.content 重新编码（meta 与向量维度不匹配时使用）。"""
        with self._lock:
            texts = [m.get("content", "") for m in self.meta]
            self.vectors = self.embedder.encode_batch(texts) if texts else self._empty_vectors()
            self._bm25_dirty = True
            self.save()

    def stats(self) -> dict:
        with self._lock:
            meta = self.meta
            vectors = self.vectors
        return {
            "chunks": len(meta),
            "docs": len({m.get("doc_id") for m in meta}),
            "dim": int(vectors.shape[1]) if vectors.size else getattr(self.embedder, "dim", 0),
            "embedder": type(self.embedder).__name__,
            "retrieval_mode": RETRIEVAL_MODE,
        }


STORE = VectorStore()
