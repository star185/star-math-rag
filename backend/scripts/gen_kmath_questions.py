# -*- coding: utf-8 -*-
"""kmath 真题 → Star Math 题卡：字段映射 + LLM 生成参考答案/得分点/大知识点。

特性（v2）：
- checkpoint：每完成一批立即写盘，中断不丢
- resume：已有有效答案的题自动跳过，可分批续跑
- 并发 2（网关稳定性）

用法: python scripts/gen_kmath_questions.py [N]
   N = 本次最多处理题数（默认全部未完成）
输入: backend/data/kmath_raw.json（fetch_kmath.py 产物）
输出: backend/data/kmath_questions.json
"""
from __future__ import annotations

import concurrent.futures
import html
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # backend/ 加入路径
from app import llm as llm_mod

llm_mod.LLM_MAX_TOKENS = 1200  # 数学解答长，提高输出上限


def llm_full(messages: list[dict]) -> str:
    parts: list[str] = []
    for item in llm_mod.stream_chat_completion(messages):
        if isinstance(item, tuple):
            continue
        parts.append(item)
    return "".join(parts)


ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "kmath_raw.json"
OUT = ROOT / "data" / "kmath_questions.json"

TYPE_MAP = {"解答题": "综合型", "证明题": "综合型", "填空题": "简答型", "单选题": "概念型"}
KNOWLEDGE_LIST = ["极限与连续", "导数与微分", "一元积分", "多元微分", "级数", "微分方程", "线性代数", "概率统计"]
KNOW_STR = "、".join(KNOWLEDGE_LIST)


def clean(text: str) -> str:
    if not text:
        return ""
    return html.unescape(text).replace("\u3000", " ").strip()


def extract_json(text: str) -> dict:
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    s, e = text.find("{"), text.rfind("}")
    if s >= 0 and e > s:
        return json.loads(text[s:e + 1])
    return json.loads(text)


def is_good(q: dict) -> bool:
    pts = q.get("answer_points") or []
    steps = q.get("calc_steps") or ""
    return len(pts) >= 2 and bool(str(steps).strip()) and pts[0] != "参考答案生成失败"


def gen_answer(q: dict) -> dict:
    question = q["question"]
    paper = q.get("papername", "")
    prompt = (
        "你是数学解题专家。请解答以下数学题，提炼得分点，并归类大知识点。\n\n"
        f"题目：{question}\n"
        f"（来源：{paper}）\n\n"
        '只输出一个 JSON 对象（不要代码块围栏、不要任何其他文字）：\n'
        '{"answer": "完整解答过程（使用 LaTeX 公式，逻辑严谨、步骤完整，控制在 250 字以内）", '
        '"points": ["得分点1", "得分点2", "得分点3"], '
        '"knowledge": "大知识点"}\n\n'
        "要求：\n"
        "- answer 直接给出解题过程，含关键公式推导与结论，公式用 $...$ 包裹，精炼不冗长\n"
        "- points 恰好 3 条，每条一句话，覆盖关键变形/关键定理/关键结论\n"
        f"- knowledge 只能从以下 8 类中选最匹配的一个，不得自造：{KNOW_STR}\n"
        "注意：输出总长不要超过 950 字符。\n"
    )
    for attempt in range(3):
        try:
            text = llm_full([{"role": "user", "content": prompt}])
            obj = extract_json(text)
            answer = str(obj.get("answer", "")).strip()
            points = [str(p).strip() for p in (obj.get("points") or []) if str(p).strip()]
            knowledge = str(obj.get("knowledge", "")).strip()
            if knowledge not in KNOWLEDGE_LIST:
                knowledge = {"gaodengdaishu": "线性代数", "shuxuefenxi": "一元积分"}.get(q.get("area", ""), "导数与微分")
            if answer and points:
                return {"answer": answer, "points": points, "knowledge": knowledge}
        except Exception:
            time.sleep(1.0)
    return {"answer": "", "points": ["参考答案生成失败，请自行解答"], "knowledge": "导数与微分"}


def main() -> int:
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    items = raw["questions"]

    # 现有缓存（resume）
    existing: dict[int, dict] = {}
    if OUT.exists():
        try:
            for q in json.loads(OUT.read_text(encoding="utf-8")).get("questions", []):
                existing[q["id"]] = q
        except Exception:
            existing = {}

    cards = []
    for it in items:
        qid = it["id"]
        if qid in existing and is_good(existing[qid]):
            cards.append(existing[qid])  # 沿用有效缓存
            continue
        qtype = TYPE_MAP.get(it["typesname"], "综合型")
        cards.append({
            "id": qid,
            "type": qtype,
            "course": "分析",
            "module": "分析",
            "difficulty": "L2",
            "mode": "short",
            "question": clean(it["question"]),
            "answer_points": [],
            "calc_steps": "",
            "expected_source": {"file": "", "title": it.get("papername", ""), "section": ""},
            "keywords": [],
            "knowledge": "",
            "source": {"site": "kmath.cn", "kmath_id": qid, "area": it.get("area", ""),
                       "typesname": it.get("typesname", ""), "hard": it.get("hard", ""),
                       "papername": it.get("papername", "")},
        })

    todo = [c for c in cards if not is_good(c)]
    if not todo:
        print(f"全部 {len(cards)} 题均已有效，无需生成", flush=True)
        _save(cards)
        return 0
    n = int(sys.argv[1]) if len(sys.argv) > 1 else len(todo)
    todo = todo[:n]
    print(f"本次处理 {len(todo)} 题（总 {len(cards)}，沿用 {len(cards) - len(todo)}）", flush=True)

    t0 = time.time()
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        futures = {ex.submit(gen_answer, c): c for c in todo}
        for fut in concurrent.futures.as_completed(futures):
            c = futures[fut]
            try:
                res = fut.result()
            except Exception as e:
                res = {"answer": "", "points": ["参考答案生成失败"], "error": str(e)}
            c["answer_points"] = res["points"]
            c["calc_steps"] = res.get("answer", "")
            c["knowledge"] = res.get("knowledge", "导数与微分")
            done += 1
            ok = is_good(c)
            if done % 4 == 0 or ok:
                _save(cards)  # checkpoint
            print(f"[{done}/{len(todo)}] #{c['id']} {'OK' if ok else 'FAIL'} 得分点 {len(res['points'])} 知识={c['knowledge']}", flush=True)

    _save(cards)
    ok_n = sum(1 for c in cards if is_good(c))
    print(f"批完成：本次 {len(todo)}，累计有效 {ok_n}/{len(cards)}（耗时 {time.time()-t0:.0f}s）→ {OUT}", flush=True)
    return 0


def _save(cards):
    data = {
        "name": "Star Math 真题集（kmath.cn）",
        "version": "1.2",
        "size": len(cards),
        "domain": "analysis",
        "source_kb": "kmath.cn 高等数学/高等代数/数学分析真题",
        "questions": cards,
    }
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(OUT)


if __name__ == "__main__":
    raise SystemExit(main())
