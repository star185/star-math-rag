# -*- coding: utf-8 -*-
"""检索评测：用测试问题集的期望来源计算 Hit@1/3/5 与 MRR，对比 vector / keyword / hybrid 三种策略。

借鉴 RAGFlow 的 Retrieval Test 思路：以“期望来源文档是否被召回”为检索质量指标，
为论文提供可量化的对比实验数据。

用法：
  cd backend
  .venv/Scripts/python.exe scripts/eval_retrieval.py            # 全部题目
  .venv/Scripts/python.exe scripts/eval_retrieval.py --limit 20 # 快速验证前 20 题
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_conn, init_db  # noqa: E402
from app.rag import retrieve  # noqa: E402

MODES = ["vector", "keyword", "hybrid"]
TOP_K = 5
OUT_JSON = Path(__file__).resolve().parent.parent / "storage" / "eval_retrieval.json"


def _norm(p: str) -> str:
    return str(p or "").replace("\\", "/").strip().lower()


def build_doc_map() -> dict[str, list[int]]:
    """把 expected_source.file（相对知识库根目录）映射到 documents.id。

    匹配依据：documents.path 的规范化后缀 == expected 文件相对路径；
    失败时再尝试按文件名 stem 匹配。
    """
    init_db()
    map_by_suffix: dict[str, list[int]] = {}
    map_by_stem: dict[str, list[int]] = {}
    with get_conn() as conn:
        rows = conn.execute("SELECT id,name,path,part FROM documents").fetchall()
    for r in rows:
        d = dict(r)
        if d.get("path"):
            map_by_suffix.setdefault(_norm(d["path"]), []).append(d["id"])
        stem = Path(d.get("name", "")).stem.lower()
        map_by_stem.setdefault(stem, []).append(d["id"])
    return map_by_suffix, map_by_stem


def resolve_expected(file: str, by_suffix: dict, by_stem: dict) -> list[int]:
    f = _norm(file)
    # 1) 后缀匹配（最可靠）
    for path, ids in by_suffix.items():
        if path.endswith(f):
            return ids
    # 2) 文件名 stem 匹配
    stem = Path(f).stem
    if stem in by_stem:
        return by_stem[stem]
    # 3) 去掉目录后的相对路径出现在 path 中
    for path, ids in by_suffix.items():
        if f.split("/")[-1] and f.split("/")[-1] in path:
            return ids
    return []


def eval_mode(questions, mode: str, top_k: int) -> dict:
    hits = {"hit1": 0, "hit3": 0, "hit5": 0}
    mrr_sum = 0.0
    misses = []
    # 分模块统计（F11：分析 / 线性代数 / 概率统计）
    mod_stats: dict[str, dict] = {}
    for q in questions:
        src = q.get("expected_source") or {}
        expected_ids = q.get("_expected_ids") or []
        mod = str(q.get("course") or q.get("module") or "未知")
        ms = mod_stats.setdefault(mod, {"hit1": 0, "hit3": 0, "hit5": 0, "mrr": 0.0, "n": 0})
        if not expected_ids:
            ms["n"] += 1
            continue
        results = retrieve(q["question"], top_k=top_k, mode=mode)
        rank = None
        for i, h in enumerate(results, 1):
            if int(h.get("doc_id", -1)) in expected_ids:
                rank = i
                break
        if rank:
            if rank <= 1:
                hits["hit1"] += 1
                ms["hit1"] += 1
            if rank <= 3:
                hits["hit3"] += 1
                ms["hit3"] += 1
            if rank <= 5:
                hits["hit5"] += 1
                ms["hit5"] += 1
            mrr_sum += 1.0 / rank
            ms["mrr"] += 1.0 / rank
        else:
            misses.append(
                {
                    "id": q.get("id"),
                    "question": q["question"],
                    "expected": src.get("file", ""),
                    "retrieved": [(h.get("doc_name"), h.get("doc_id")) for h in results[:3]],
                }
            )
        ms["n"] += 1
    n = len(questions)
    per_module = []
    for mod, m in sorted(mod_stats.items()):
        per_module.append({
            "module": mod,
            "n": m["n"],
            "hit@1": round(m["hit1"] / m["n"], 4) if m["n"] else 0,
            "hit@3": round(m["hit3"] / m["n"], 4) if m["n"] else 0,
            "hit@5": round(m["hit5"] / m["n"], 4) if m["n"] else 0,
            "mrr": round(m["mrr"] / m["n"], 4) if m["n"] else 0,
        })
    return {
        "mode": mode,
        "n": n,
        "hit@1": round(hits["hit1"] / n, 4) if n else 0,
        "hit@3": round(hits["hit3"] / n, 4) if n else 0,
        "hit@5": round(hits["hit5"] / n, 4) if n else 0,
        "mrr": round(mrr_sum / n, 4) if n else 0,
        "miss_count": len(misses),
        "misses": misses[:8],
        "per_module": per_module,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-k", type=int, default=TOP_K)
    ap.add_argument("--limit", type=int, default=0, help="只评测前 N 题（0=全部）")
    args = ap.parse_args()

    q_path = Path(__file__).resolve().parent.parent / "data" / "test_questions.json"
    data = json.loads(q_path.read_text(encoding="utf-8"))
    questions = data.get("questions", [])
    if args.limit:
        questions = questions[: args.limit]
    print(f"评测问题数: {len(questions)}")

    by_suffix, by_stem = build_doc_map()
    mapped = 0
    for q in questions:
        src = q.get("expected_source") or {}
        q["_expected_ids"] = resolve_expected(src.get("file", ""), by_suffix, by_stem)
        if q["_expected_ids"]:
            mapped += 1
    print(f"期望来源可映射到知识库文档: {mapped}/{len(questions)}")
    print()

    results = [eval_mode(questions, m, args.top_k) for m in MODES]

    print(f"{'策略':<10}{'Hit@1':>10}{'Hit@3':>10}{'Hit@5':>10}{'MRR':>10}{'未命中':>8}")
    print("-" * 56)
    for r in results:
        print(
            f"{r['mode']:<10}{r['hit@1']:>10.4f}{r['hit@3']:>10.4f}{r['hit@5']:>10.4f}{r['mrr']:>10.4f}{r['miss_count']:>8}"
        )

    # F11：分模块评测表
    print()
    print("=== 分模块评测（hybrid） ===")
    print(f"{'模块':<10}{'N':>6}{'Hit@1':>10}{'Hit@3':>10}{'Hit@5':>10}{'MRR':>10}")
    print("-" * 56)
    mod_rows = []
    for r in results:
        if r["mode"] != "hybrid":
            continue
        for m in r.get("per_module", []):
            mod_rows.append(m)
            print(
                f"{m['module']:<10}{m['n']:>6}{m['hit@1']:>10.4f}{m['hit@3']:>10.4f}{m['hit@5']:>10.4f}{m['mrr']:>10.4f}"
            )

    out = {
        "generated_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "questions": len(questions),
        "top_k": args.top_k,
        "modes": [{"mode": r["mode"], "hit@1": r["hit@1"], "hit@3": r["hit@3"], "hit@5": r["hit@5"], "mrr": r["mrr"], "miss_count": r["miss_count"], "per_module": r.get("per_module", [])} for r in results],
        "miss_samples": {r["mode"]: r["misses"] for r in results},
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入: {OUT_JSON}")


if __name__ == "__main__":
    main()
