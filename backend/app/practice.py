# -*- coding: utf-8 -*-
"""做题练习模块（M1–M5）：出题 / 判分 / 解析 / 错题本 / 导出。

- GET  /api/practice/paper    抽题（module/qtype/difficulty/count，不含答案要点）
- POST /api/practice/grade    判分（choice/judge 本地比对；short/calc LLM 或关键词判分）
- POST /api/practice/explain  解析（参考答案要点 + 计算步骤）
- GET  /api/practice/records  做题记录 / 错题本（module 过滤、只看错题）
- POST /api/practice/save     保存一次答题记录
- GET  /api/practice/export   导出今日练习 / 错题（Markdown）
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .config import APP_NAME, DATA_DIR
from .db import get_conn
from .deps import get_current_user
from .llm import llm_configured, stream_chat_completion
from . import quota

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/practice", tags=["practice"])

QUESTIONS_PATH = DATA_DIR / "test_questions.json"


# ---------- 鉴权：统一使用 app/deps.py，避免多份实现漂移 ----------
def _load_questions() -> list[dict]:
    questions: list[dict] = []
    if QUESTIONS_PATH.exists():
        data = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
        questions.extend(data.get("questions", []))
    kmath_path = DATA_DIR / "kmath_questions.json"
    if kmath_path.exists():
        kdata = json.loads(kmath_path.read_text(encoding="utf-8"))
        questions.extend(kdata.get("questions", []))
    if not questions:
        raise HTTPException(status_code=500, detail="题目集不存在，请先运行 scripts/gen_test_questions.py")
    return questions


def _find(qid: int) -> dict:
    for q in _load_questions():
        if q.get("id") == qid:
            return q
    raise HTTPException(status_code=404, detail="题目不存在")


def _as_list(v) -> list:
    """统一为列表：真题集的 calc_steps 是完整解答字符串，转成单元素列表（前端按数组渲染）。"""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        return [v] if v.strip() else []
    return []


def _public(q: dict) -> dict:
    """题面（不含 answer_points / calc_steps / answer_index）。"""
    return {
        "qid": q.get("id"),
        "type": q.get("type"),
        "module": q.get("module") or q.get("course") or "",
        "difficulty": q.get("difficulty", "L2"),
        "mode": q.get("mode", "short"),
        "question": q.get("question"),
        "options": q.get("options"),
        "knowledge": q.get("knowledge", ""),
    }


# ---------- M1 出题 ----------
@router.get("/paper")
def paper(
    module: str = "",
    qtype: str = "",
    difficulty: str = "",
    count: int = 5,
    knowledge: str = "",
    user: dict = Depends(get_current_user),
):
    pool = _load_questions()
    if module:
        pool = [q for q in pool if (q.get("module") or q.get("course")) == module]
    if knowledge:
        pool = [q for q in pool if q.get("knowledge") == knowledge]
    if qtype:
        if qtype == "真题":
            pool = [q for q in pool if (q.get("source") or {}).get("site") == "kmath.cn"]
        elif qtype == "概念":
            pool = [q for q in pool if not (q.get("source") or {}).get("site")]
        else:
            pool = [q for q in pool if q.get("type") == qtype]
    if difficulty:
        pool = [q for q in pool if q.get("difficulty") == difficulty]
    if not pool:
        raise HTTPException(status_code=404, detail="没有符合条件的题目，请放宽筛选条件")
    random.seed(time.time())
    picked = random.sample(pool, min(count, len(pool)))
    return {"questions": [_public(q) for q in picked], "count": len(picked)}


# ---------- M2 判分 ----------
class GradeIn(BaseModel):
    qid: int
    answer: str = ""
    mode: str = ""


def _kw_grade(answer: str, points: list[str]) -> tuple[bool, float]:
    """关键词判分：参考答案要点中至少 50% 的要点在回答中出现关键词则判对。"""
    if not points:
        return bool(answer.strip()), 1.0 if answer.strip() else 0.0
    if not answer or not answer.strip():
        return False, 0.0

    def keys_of(p: str) -> list[str]:
        compact = re.sub(r"[，。；：！？、\s（）()\[\]【】\"'「」]+", "", p)
        out = set()
        if 2 <= len(compact) <= 12:
            out.add(compact)
        for n in (4, 3, 2):
            for i in range(max(0, len(compact) - n + 1)):
                t = compact[i : i + n]
                if re.fullmatch(r"[一-鿿a-zA-Z0-9_]+", t):
                    out.add(t)
        return sorted(out, key=lambda x: -len(x))[:6]

    ans_flat = re.sub(r"\s+", "", answer or "")
    hit = 0
    for p in points:
        kws = keys_of(p)
        if any(k and k in ans_flat for k in kws):
            hit += 1
    ratio = hit / len(points)
    return ratio >= 0.5, round(ratio, 2)


def _grade_answer(q: dict, answer: str, uid: int | None = None) -> dict:
    """服务端判定：choice/judge 本地比对，short/calc 走 LLM 或关键词。

    grade 与 save 共用这一份逻辑，避免"判分一次、入库一次"两套口径。
    uid 不为空且真的调用了 LLM 时，占用当日配额。
    """
    qmode = q.get("mode", "short")
    answer = (answer or "").strip()

    # choice / judge：本地比对
    if qmode == "choice":
        options = q.get("options") or []
        ans_idx = q.get("answer_index")
        try:
            sel = int(answer)
        except Exception:
            sel = -1
        correct = sel == ans_idx and 0 <= sel < len(options)
        return {"correct": bool(correct), "score": 1.0 if correct else 0.0,
                "answer_index": ans_idx, "correct_answer": options[ans_idx] if 0 <= ans_idx < len(options) else ""}
    if qmode == "judge":
        true_vals = {"1", "true", "对", "正确", "√", "是"}
        false_vals = {"0", "false", "错", "错误", "×", "否"}
        norm = answer.lower()
        if norm in true_vals or norm in false_vals:
            correct = (norm in true_vals) == bool(q.get("answer"))
            return {"correct": bool(correct), "score": 1.0 if correct else 0.0, "correct_answer": "对" if q.get("answer") else "错"}
        return {"correct": False, "score": 0.0, "correct_answer": "对" if q.get("answer") else "错"}

    # short / calc：LLM 判分（已配置时），否则关键词判分
    points = q.get("answer_points") or []
    calc_steps = q.get("calc_steps") or []
    if llm_configured():
        try:
            if uid is not None:
                quota.consume(uid, cost=1)
            system = (
                "你是数学判卷助手。请依据参考答案要点判断学生答案是否正确，"
                "只输出 JSON：{\"correct\":true/false,\"score\":0-1,\"feedback\":\"一句话点评\"}。\n"
                f"参考答案要点：\n" + "\n".join(f"- {p}" for p in points)
                + (("\n计算步骤：\n" + "\n".join(f"- {s}" for s in calc_steps)) if calc_steps else "")
            )
            msgs = [
                {"role": "system", "content": system},
                {"role": "user", "content": f"学生答案：{answer}"},
            ]
            buf = []
            for tok in stream_chat_completion(msgs):
                buf.append(tok)
            text = "".join(buf)
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                obj = json.loads(m.group(0))
                return {"correct": bool(obj.get("correct")), "score": float(obj.get("score", 0)),
                        "feedback": obj.get("feedback", ""), "correct_answer": "、".join(points)}
        except Exception:
            pass
    correct, ratio = _kw_grade(answer, points)
    return {"correct": correct, "score": ratio, "correct_answer": "、".join(points)}


# 判分结果短期缓存：save 紧随 grade 时直接复用，避免同一题付两次 LLM 费用
_GRADE_CACHE_TTL = 1800
_grade_cache: dict[tuple[int, int], tuple[float, dict]] = {}


def _cached_grade(uid: int, q: dict, answer: str) -> dict:
    key = (uid, int(q.get("id") or 0))
    hit = _grade_cache.get(key)
    if hit and time.time() - hit[0] < _GRADE_CACHE_TTL:
        cached = hit[1]
        # 只有答案一致时才复用，改了答案必须重判
        if cached.get("_answer") == (answer or "").strip():
            return {k: v for k, v in cached.items() if not k.startswith("_")}
    result = _grade_answer(q, answer, uid=uid)
    _grade_cache[key] = (time.time(), {**result, "_answer": (answer or "").strip()})
    return result


@router.post("/grade")
def grade(body: GradeIn, user: dict = Depends(get_current_user)):
    q = _find(body.qid)
    result = _cached_grade(user.get("uid"), q, body.answer)
    return result


# ---------- M3 解析 ----------
class ExplainIn(BaseModel):
    qid: int


@router.post("/explain")
def explain(body: ExplainIn, user: dict = Depends(get_current_user)):
    q = _find(body.qid)
    src = q.get("expected_source") or {}
    return {
        "qid": q.get("id"),
        "question": q.get("question"),
        "type": q.get("type"),
        "module": q.get("module") or q.get("course"),
        "answer_points": q.get("answer_points") or [],
        "calc_steps": _as_list(q.get("calc_steps")),
        "knowledge": q.get("knowledge", ""),
        "correct_answer": q.get("options")[q.get("answer_index")] if q.get("mode") == "choice" and q.get("options") and q.get("answer_index") is not None else "",
        "expected_source": src,
    }


# ---------- M4 错题本 / 记录 ----------
class SaveIn(BaseModel):
    qid: int
    question: str = ""
    answer: str = ""
    correct: bool = False
    module: str = ""
    qtype: str = ""
    difficulty: str = ""
    mode: str = "short"


@router.post("/save")
def save(body: SaveIn, user: dict = Depends(get_current_user)):
    """保存一次答题记录。

    判分以服务端重算为准（客户端提交的 correct 一律忽略），
    否则做题记录与错题本可以直接被伪造成"全对"。
    """
    q = _find(body.qid)
    result = _cached_grade(user.get("uid"), q, body.answer)
    question = q.get("question") or body.question
    module = q.get("module") or q.get("course") or body.module
    qtype = q.get("type") or body.qtype
    difficulty = q.get("difficulty") or body.difficulty
    mode = q.get("mode") or body.mode
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO practice_records (user_id, qid, question, module, qtype, difficulty, mode, answer, correct, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (user.get("uid"), body.qid, question, module, qtype,
             difficulty, mode, (body.answer or "").strip(), 1 if result["correct"] else 0, time.time()),
        )
        rid = cur.lastrowid
    return {"id": rid, "saved": True, "correct": bool(result["correct"]), "score": result.get("score", 0)}


@router.get("/records")
def records(module: str = "", wrong_only: bool = False, limit: int = 200, user: dict = Depends(get_current_user)):
    sql = "SELECT * FROM practice_records WHERE user_id=?"
    args: list = [user.get("uid")]
    if module:
        sql += " AND module=?"
        args.append(module)
    if wrong_only:
        sql += " AND correct=0"
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "qid": r["qid"], "question": r["question"],
            "module": r["module"], "qtype": r["qtype"], "difficulty": r["difficulty"],
            "mode": r["mode"], "answer": r["answer"], "correct": bool(r["correct"]),
            "created_at": r["created_at"],
        })
    return {"records": out, "total": len(out)}


# ---------- M5 导出 ----------
@router.get("/export")
def export(module: str = "", wrong_only: bool = False, user: dict = Depends(get_current_user)):
    data = records(module=module, wrong_only=wrong_only, limit=500, user=user)
    recs = data["records"]
    qs = {q.get("id"): q for q in _load_questions()}
    date_str = time.strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# Star Math 做题记录（{date_str}）",
        "",
        f"- 用户：{user.get('username', '')}",
        f"- 筛选：模块={module or '全部'} · 仅错题={wrong_only}",
        f"- 题数：{len(recs)}",
        "",
        "| # | 模块 | 题型 | 题目 | 我的答案 | 判分 |",
        "|----|------|------|------|----------|------|",
    ]
    for i, r in enumerate(recs, 1):
        answer = (r["answer"] or "").replace("|", "\\|")[:60]
        question = (r["question"] or "").replace("|", "\\|")[:60]
        lines.append(f"| {i} | {r['module']} | {r['qtype'] or r['mode']} | {question} | {answer} | {'✓' if r['correct'] else '✗'} |")
    lines.append("")
    lines.append("## 错题明细（参考答案要点）")
    wrong = [r for r in recs if not r["correct"]]
    if not wrong:
        lines.append("（无错题）")
    for r in wrong:
        q = qs.get(r["qid"]) or {}
        lines.append("")
        lines.append(f"### {r['qid']}. {q.get('question') or r['question']}")
        lines.append("")
        lines.append(f"- 我的答案：{r['answer'] or '（未作答）'}")
        pts = q.get("answer_points") or []
        if pts:
            lines.append("- 参考答案要点：")
            for p in pts:
                lines.append(f"  - {p}")
        steps = q.get("calc_steps") or []
        if steps:
            lines.append("- 计算步骤：")
            for s in steps:
                lines.append(f"  - {s}")
    md = "\n".join(lines)
    fname = f"star-math-practice-{time.strftime('%Y%m%d-%H%M')}.md"
    return PlainTextResponse(
        md,
        headers={"Content-Disposition": f'attachment; filename="{fname}"', "Content-Type": "text/markdown; charset=utf-8"},
    )
