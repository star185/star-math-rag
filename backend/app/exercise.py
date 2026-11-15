# -*- coding: utf-8 -*-
"""上传解题与习题册模块（M 系列新增）：上传解析、OCR、习题识别、RAG 解题、习题册 CRUD 与导出。

- POST  /api/exercises/upload                      上传文档/图片并识别习题
- POST  /api/exercises/{source_id}/solve           SSE 流式逐题解题
- POST  /api/exercises/save                        批量存入习题册
- GET   /api/exercises                             习题册列表（筛选）
- GET   /api/exercises/{id}                        单题详情
- DELETE /api/exercises/{id}                       删除单题
- DELETE /api/exercises/source/{source_id}         删除整份来源及其题目
- GET   /api/exercises/export                      导出 Markdown
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from .config import (
    EXERCISE_MAX_MB,
    EXERCISE_UPLOAD_DIR,
    SOLVE_MAX_QUESTIONS,
    SOLVE_TOP_K,
    VISION_LLM_API_BASE,
    VISION_LLM_API_KEY,
    VISION_LLM_MODEL,
)
from .db import dumps, get_conn, loads
from .llm import chat_completion, llm_configured, stream_chat_completion
from .parsers import IMAGE_SUFFIXES, extract_docx_images, ocr_image, parse_document, parse_frontmatter
from .rag import retrieve
from .security import parse_token

router = APIRouter(prefix="/api/exercises", tags=["exercises"])

DOC_SUFFIXES = {".md", ".markdown", ".txt", ".docx", ".pdf"}
TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".docx", ".pdf"}


# ---------- 鉴权（与 main.get_current_user 同逻辑，避免循环依赖） ----------
def current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    payload = parse_token(authorization.split(" ", 1)[1].strip())
    if not payload:
        raise HTTPException(status_code=401, detail="登录已过期")
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT id,username,role,can_download FROM users WHERE id=?",
                (payload.get("uid"),),
            ).fetchone()
            if row:
                payload["username"] = row["username"]
                payload["role"] = row["role"]
                payload["can_download"] = bool(row["can_download"])
    except Exception:
        payload.setdefault("can_download", False)
    return payload


def _get_source(conn, source_id: int, uid: int):
    row = conn.execute(
        "SELECT * FROM exercise_sources WHERE id=? AND user_id=?", (source_id, uid)
    ).fetchone()
    if not row:
        raise HTTPException(404, "来源不存在或无权访问")
    return row


# ---------- 文本提取 ----------
def _vision_configured() -> bool:
    return bool((VISION_LLM_API_BASE or "").strip() and (VISION_LLM_API_KEY or "").strip() and (VISION_LLM_MODEL or "").strip())


def _vision_image_to_text(path: Path) -> str:
    """可选视觉大模型：OpenAI 兼容 image_url 消息，直接返回 Markdown 化文本。"""
    import base64
    import urllib.request

    b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".bmp": "image/bmp"}.get(path.suffix.lower(), "image/png")
    data_url = f"data:{mime};base64,{b64}"
    body = {
        "model": VISION_LLM_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请识别图中的全部数学题目，输出为 Markdown 文本：保留题号、题干、选项；公式用 $...$ 或 $$...$$ 包裹。"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "temperature": 0.1,
    }
    req = urllib.request.Request(
        VISION_LLM_API_BASE.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {VISION_LLM_API_KEY}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"] or ""


def _image_to_text(path: Path) -> str:
    if _vision_configured():
        try:
            return _vision_image_to_text(path)
        except Exception as e:
            # 视觉模型失败时回退本地 OCR
            try:
                return ocr_image(path)
            except Exception:
                raise HTTPException(500, f"图片识别失败: {e}")
    return ocr_image(path)


def _extract_source_text(dest: Path, suffix: str) -> str:
    """文档/图片 → Markdown 化文本（docx 内嵌图片并入正文）。"""
    if suffix in IMAGE_SUFFIXES:
        return _image_to_text(dest)
    text, _ = parse_document(dest)
    if suffix == ".docx":
        images = extract_docx_images(dest)
        if images:
            parts = [text]
            tmp_dir = EXERCISE_UPLOAD_DIR / "_docx_tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            for i, blob in enumerate(images, 1):
                img_path = tmp_dir / f"{time.time()*1000:.0f}_{i}.png"
                img_path.write_bytes(blob)
                try:
                    parts.append(f"\n[图片{i}]\n{ocr_image(img_path)}")
                except Exception:
                    continue
                finally:
                    img_path.unlink(missing_ok=True)
            return "\n".join(parts)
    return text


# ---------- 习题识别 ----------
_QUESTION_RE = re.compile(r"(?m)^\s*(?:第[一二三四五六七八九十百\d]+题|[（(]?\d+[)）]?[.、．]|【\d+】)\s*")


def _split_blocks(text: str) -> list[str]:
    """按题号启发式切块。"""
    text = text.strip()
    if not text:
        return []
    parts = _QUESTION_RE.split(text)
    # parts[0] 为开头无题号的前言
    blocks: list[str] = []
    if parts and parts[0].strip():
        blocks.append(parts[0].strip())
    for p in parts[1:]:
        p = p.strip()
        if p:
            blocks.append(p)
    return [b for b in blocks if b]


def _parse_questions_json(text: str) -> list[dict] | None:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return None
    qs = obj.get("questions") if isinstance(obj, dict) else obj
    if not isinstance(qs, list):
        return None
    out = []
    for i, q in enumerate(qs, 1):
        if not isinstance(q, dict) or not str(q.get("stem") or "").strip():
            continue
        out.append(
            {
                "no": q.get("no") or i,
                "type": (q.get("type") or "short") if (q.get("type") or "short") in {"choice", "judge", "short", "calc"} else "short",
                "stem": str(q.get("stem")).strip(),
                "options": [str(o) for o in (q.get("options") or []) if str(o).strip()],
                "answer": str(q.get("answer") or "").strip(),
                "points": [str(p) for p in (q.get("points") or []) if str(p).strip()],
            }
        )
    return out or None


def _extract_exercises(text: str) -> list[dict]:
    """识别习题：LLM 结构化提取（优先），无 LLM 或失败时本地回退为整块题面。"""
    if not text.strip():
        return []
    blocks = _split_blocks(text)[: SOLVE_MAX_QUESTIONS]
    questions: list[dict] = []
    if llm_configured():
        for block in blocks:
            try:
                system = (
                    "你是数学题目识别助手。请从下面的文本中提取习题，只输出 JSON：\n"
                    '{"questions":[{"no":1,"type":"choice|judge|short|calc","stem":"题面（保留公式 $...$）",'
                    '"options":["A…","B…"],"answer":"（原文含答案时填，否则空字符串）","points":["参考答案要点"]}]}\n'
                    "要求：题面完整保留；选择题/判断题必须给出 options 或明确类型；没有题目则输出 {\"questions\":[]}。"
                )
                user = f"文本内容：\n{block}"
                buf = chat_completion([{"role": "system", "content": system}, {"role": "user", "content": user}])
                parsed = _parse_questions_json(buf)
                if parsed:
                    for q in parsed:
                        q["stem"] = q["stem"] or block
                    questions.extend(parsed)
                    continue
            except Exception:
                pass
            # 单块回退：整块作为一道题
            questions.append({"no": len(questions) + 1, "type": "short", "stem": block, "options": [], "answer": "", "points": []})
    else:
        for i, block in enumerate(blocks, 1):
            questions.append({"no": i, "type": "short", "stem": block, "options": [], "answer": "", "points": []})
    # 去空题面
    questions = [q for q in questions if q.get("stem", "").strip()]
    for i, q in enumerate(questions, 1):
        q["no"] = i
    return questions[: SOLVE_MAX_QUESTIONS]


# ---------- 解题 ----------
def _estimate_difficulty(stem: str) -> str:
    if re.search(r"证明|定理|综合|应用", stem):
        return "L3"
    if re.search(r"定义|概念|判断|选择|简述|说明", stem):
        return "L1"
    return "L2"


def _solve_prompt(stem: str, options: list[str]) -> list[dict]:
    contexts = retrieve(stem, top_k=SOLVE_TOP_K, mode="hybrid")
    ctx_text = ""
    if contexts:
        blocks = []
        for i, c in enumerate(contexts, 1):
            head = f"[{i}] {c.get('doc_name') or '知识片段'}{' · ' + (c.get('section') or '') if c.get('section') else ''}"
            body = (c.get("parent_content") or c.get("content") or "").strip()
            blocks.append(f"{head}\n{body}")
        ctx_text = "\n\n".join(blocks)
    system = (
        "你是严谨的数学解题助手。请按以下要求解答题目：\n"
        "1. 输出四节：【思路分析】【分步解答】【最终答案】【知识点】。\n"
        "2. 数学公式必须用 $...$（行内）或 $$...$$（块级）包裹：独立成行的公式一律用 $$...$$ 独占一行；严禁输出未包裹的裸 LaTeX。比较符号 <、> 前后保留空格，不要输出 HTML 实体。\n"
        "3. 【最终答案】简洁明确：选择题给出选项字母与内容，判断题给出「对/错」，计算与简答给出结果。\n"
        "4. 【知识点】给出 1-3 个知识点标签，用顿号分隔。\n"
        "5. 题目信息不完整时如实说明缺什么，不要臆测。\n"
        + (f"\n以下为知识库检索到的参考资料，若与题目相关可引用（关键结论句末标注 [n]）：\n{ctx_text}" if ctx_text else "")
    )
    q_text = stem.strip()
    if options:
        q_text += "\n选项：\n" + "\n".join(f"{chr(65 + i)}. {o}" for i, o in enumerate(options))
    return [{"role": "system", "content": system}, {"role": "user", "content": f"题目：\n{q_text}"}]


def _extract_section(text: str, name: str) -> str:
    """提取【xxx】小节内容（到下一个【或文末）。"""
    m = re.search(rf"【{name}】\s*(.*?)(?=\n*【|$)", text, re.S)
    return m.group(1).strip() if m else ""


def _solve_one(question: dict) -> dict:
    """单题 RAG 解题，返回解答结构化结果。"""
    stem = question.get("stem", "")
    try:
        messages = _solve_prompt(stem, question.get("options") or [])
        buf: list[str] = []
        for tok in stream_chat_completion(messages):
            if isinstance(tok, tuple):
                continue
            buf.append(tok)
        full = "".join(buf)
        solution = full.strip() or "（未能生成解答，请稍后重试或检查大模型配置）"
        explanation = _extract_section(full, "思路分析")
        final_answer = _extract_section(full, "最终答案") or _extract_section(full, "答案")
        knowledge = _extract_section(full, "知识点") or "数学"
        return {
            "answer": final_answer or question.get("answer") or "",
            "solution": solution,
            "explanation": explanation,
            "knowledge": knowledge,
            "difficulty": _estimate_difficulty(stem),
            "sources": retrieve(stem, top_k=SOLVE_TOP_K, mode="hybrid"),
        }
    except Exception as e:
        return {
            "answer": question.get("answer") or "",
            "solution": f"（解题失败: {e}）",
            "explanation": "",
            "knowledge": "数学",
            "difficulty": _estimate_difficulty(stem),
            "sources": [],
        }


# ---------- API ----------
class SolveIn(BaseModel):
    questions: list[dict] = []


class SaveItem(BaseModel):
    qno: int = 0
    type: str = "short"
    difficulty: str = "L2"
    question: str = ""
    options: list[str] = []
    answer: str = ""
    solution: str = ""
    explanation: str = ""
    knowledge: str = ""


class SaveIn(BaseModel):
    source_id: int
    items: list[SaveItem] = []


@router.post("/upload")
def upload_exercises(
    file: UploadFile = File(...),
    user: dict = Depends(current_user),
):
    """上传文档/图片，解析并识别习题，登记来源后返回题目列表。"""
    filename = Path(file.filename or "upload.bin").name
    suffix = Path(filename).suffix.lower()
    if suffix not in DOC_SUFFIXES and suffix not in IMAGE_SUFFIXES:
        raise HTTPException(400, f"不支持格式: {suffix}（支持 md/txt/docx/pdf 与 png/jpg/jpeg/webp/bmp）")
    EXERCISE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = EXERCISE_UPLOAD_DIR / f"{int(time.time() * 1000)}_{filename}"
    size = 0
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
        size = dest.stat().st_size
    if size > EXERCISE_MAX_MB * 1024 * 1024:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"文件过大，上限 {EXERCISE_MAX_MB}MB")
    try:
        text = _extract_source_text(dest, suffix)
        if not (text or "").strip():
            dest.unlink(missing_ok=True)
            raise HTTPException(400, "未能从文件中识别出文字内容（图片可能不清晰）")
        questions = _extract_exercises(text)
        if not questions:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, "未识别到习题，请确认文件内容")
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO exercise_sources(user_id,name,source_type,path,format,size,question_count,questions_json,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    user.get("uid"),
                    Path(filename).stem,
                    "image" if suffix in IMAGE_SUFFIXES else "doc",
                    str(dest),
                    suffix.lstrip("."),
                    size,
                    len(questions),
                    dumps(questions),
                    time.time(),
                ),
            )
            source_id = cur.lastrowid
        return {
            "source_id": source_id,
            "name": Path(filename).stem,
            "source_type": "image" if suffix in IMAGE_SUFFIXES else "doc",
            "questions": questions,
        }
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(500, f"解析失败: {e}")


@router.post("/{source_id}/solve")
def solve(source_id: int, body: SolveIn, user: dict = Depends(current_user)):
    """SSE 流式逐题解题。"""
    with get_conn() as conn:
        src = _get_source(conn, source_id, user.get("uid"))
        questions = body.questions or loads(src.get("questions_json"), [])
    if not questions:
        raise HTTPException(400, "没有可解题的题目")

    def event_stream():
        try:
            yield f"data: {json.dumps({'type': 'solve_start', 'source_id': source_id, 'count': len(questions)}, ensure_ascii=False)}\n\n"
            for q in questions:
                qno = q.get("no") or 0
                meta = {"qno": qno, "type": q.get("type", "short"), "question": q.get("stem", ""), "options": q.get("options", [])}
                yield f"data: {json.dumps({'type': 'solve_meta', 'data': meta}, ensure_ascii=False)}\n\n"
                result = _solve_one(q)
                yield f"data: {json.dumps({'type': 'solve_done', 'data': {'qno': qno, **result}}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'source_id': source_id}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'detail': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/save")
def save_items(body: SaveIn, user: dict = Depends(current_user)):
    """批量存入习题册（同来源同题号已存在时更新）。"""
    if not body.items:
        raise HTTPException(400, "没有可保存的题目")
    with get_conn() as conn:
        _get_source(conn, body.source_id, user.get("uid"))
        saved = 0
        for it in body.items:
            if not (it.question or "").strip():
                continue
            exists = conn.execute(
                "SELECT id FROM exercise_items WHERE user_id=? AND source_id=? AND qno=?",
                (user.get("uid"), body.source_id, it.qno),
            ).fetchone()
            if exists:
                conn.execute(
                    "UPDATE exercise_items SET type=?,difficulty=?,question=?,options_json=?,answer=?,solution=?,explanation=?,knowledge=?,status='done' WHERE id=?",
                    (
                        it.type or "short",
                        it.difficulty or "L2",
                        it.question,
                        dumps(it.options),
                        it.answer,
                        it.solution,
                        it.explanation,
                        it.knowledge,
                        exists["id"],
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO exercise_items(user_id,source_id,qno,type,difficulty,question,options_json,answer,solution,explanation,knowledge,status,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        user.get("uid"),
                        body.source_id,
                        it.qno,
                        it.type or "short",
                        it.difficulty or "L2",
                        it.question,
                        dumps(it.options),
                        it.answer,
                        it.solution,
                        it.explanation,
                        it.knowledge,
                        "done",
                        time.time(),
                    ),
                )
            saved += 1
        conn.execute(
            "UPDATE exercise_sources SET question_count=? WHERE id=?",
            (conn.execute("SELECT COUNT(*) AS c FROM exercise_items WHERE source_id=?", (body.source_id,)).fetchone()["c"], body.source_id),
        )
    return {"saved": saved}


@router.get("")
def list_items(
    source_type: str = "",
    qtype: str = "",
    status: str = "",
    limit: int = 200,
    user: dict = Depends(current_user),
):
    """习题册列表（按来源分组信息 + 题目列表）。"""
    sql = "SELECT i.*, s.name AS source_name, s.source_type AS source_type, s.path AS source_path, s.format AS source_format, s.created_at AS source_created_at FROM exercise_items i JOIN exercise_sources s ON i.source_id=s.id WHERE i.user_id=?"
    args: list = [user.get("uid")]
    if source_type:
        sql += " AND s.source_type=?"
        args.append(source_type)
    if qtype:
        sql += " AND i.type=?"
        args.append(qtype)
    if status:
        sql += " AND i.status=?"
        args.append(status)
    sql += " ORDER BY i.id DESC LIMIT ?"
    args.append(min(limit, 500))
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
        stats = conn.execute(
            "SELECT COUNT(*) AS total, COUNT(DISTINCT source_id) AS srcs, "
            "SUM(CASE WHEN created_at>? THEN 1 ELSE 0 END) AS today FROM exercise_items WHERE user_id=?",
            (time.time() - 86400, user.get("uid")),
        ).fetchone()
    items = []
    for r in rows:
        items.append(
            {
                "id": r["id"],
                "source_id": r["source_id"],
                "qno": r["qno"],
                "type": r["type"],
                "difficulty": r["difficulty"],
                "question": r["question"],
                "options": loads(r["options_json"], []),
                "answer": r["answer"],
                "solution": r["solution"],
                "explanation": r["explanation"],
                "knowledge": r["knowledge"],
                "status": r["status"],
                "created_at": r["created_at"],
                "source_name": r["source_name"],
                "source_type": r["source_type"],
                "source_path": r["source_path"],
                "source_format": r["source_format"],
            }
        )
    return {
        "items": items,
        "total": len(items),
        "stats": {"total": stats["total"], "sources": stats["srcs"], "today": stats["today"] or 0},
    }


@router.get("/export")
def export_exercises(user: dict = Depends(current_user)):
    data = list_items(limit=500, user=user)
    recs = data["items"]
    date_str = time.strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# Star Math 习题册（{date_str}）",
        "",
        f"- 用户：{user.get('username', '')}",
        f"- 题数：{len(recs)}",
        "",
        "| # | 来源 | 题型 | 难度 | 题目 | 知识点 | 状态 |",
        "|----|------|------|------|------|--------|------|",
    ]
    for i, r in enumerate(recs, 1):
        q = (r["question"] or "").replace("|", "\\|")
        q = q if len(q) <= 60 else q[:60] + "…"
        lines.append(
            f"| {i} | {r['source_name']} | {r['type']} | {r['difficulty']} | {q} | {r['knowledge']} | {r['status']} |"
        )
    lines.append("")
    lines.append("## 题目与解答明细")
    for r in recs:
        lines.append("")
        lines.append(f"### {r['id']}. {r['question']}")
        if r["options"]:
            lines.append("")
            for j, o in enumerate(r["options"]):
                lines.append(f"- {chr(65 + j)}. {o}")
        lines.append("")
        lines.append(f"**最终答案：** {r['answer'] or '（未给出）'}")
        if r["explanation"]:
            lines.append("")
            lines.append(f"**思路分析：** {r['explanation']}")
        if r["solution"]:
            lines.append("")
            lines.append("**分步解答：**")
            lines.append("")
            for ln in (r["solution"] or "").splitlines():
                lines.append(ln)
        lines.append("")
        lines.append(f"知识点：{r['knowledge']} · 来源：{r['source_name']}")
    md = "\n".join(lines)
    fname = f"star-math-exercise-book-{time.strftime('%Y%m%d-%H%M')}.md"
    return PlainTextResponse(
        md,
        headers={"Content-Disposition": f'attachment; filename="{fname}"', "Content-Type": "text/markdown; charset=utf-8"},
    )


@router.get("/{item_id}")
def get_item(item_id: int, user: dict = Depends(current_user)):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT i.*, s.name AS source_name, s.source_type AS source_type, s.path AS source_path, s.format AS source_format "
            "FROM exercise_items i JOIN exercise_sources s ON i.source_id=s.id WHERE i.id=? AND i.user_id=?",
            (item_id, user.get("uid")),
        ).fetchone()
        if not row:
            raise HTTPException(404, "题目不存在或无权访问")
    return {
        "id": row["id"],
        "source_id": row["source_id"],
        "qno": row["qno"],
        "type": row["type"],
        "difficulty": row["difficulty"],
        "question": row["question"],
        "options": loads(row["options_json"], []),
        "answer": row["answer"],
        "solution": row["solution"],
        "explanation": row["explanation"],
        "knowledge": row["knowledge"],
        "status": row["status"],
        "created_at": row["created_at"],
        "source_name": row["source_name"],
        "source_type": row["source_type"],
        "source_path": row["source_path"],
        "source_format": row["source_format"],
    }


@router.delete("/{item_id}")
def delete_item(item_id: int, user: dict = Depends(current_user)):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM exercise_items WHERE id=? AND user_id=?", (item_id, user.get("uid"))
        ).fetchone()
        if not row:
            raise HTTPException(404, "题目不存在或无权访问")
        conn.execute("DELETE FROM exercise_items WHERE id=?", (item_id,))
    return {"ok": True}


@router.delete("/source/{source_id}")
def delete_source(source_id: int, user: dict = Depends(current_user)):
    with get_conn() as conn:
        src = _get_source(conn, source_id, user.get("uid"))
        conn.execute("DELETE FROM exercise_items WHERE source_id=?", (source_id,))
        conn.execute("DELETE FROM exercise_sources WHERE id=?", (source_id,))
        path = src["path"]
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass
    return {"ok": True}


# ---------- 导出 Markdown（置于 /{item_id} 之前定义，避免路径参数抢先匹配） ----------


# ---------- 知识点概念卡精确匹配：只有知识库里真有该概念才返回文档 ----------
import difflib as _difflib


def _norm_concept(t: str) -> str:
    if not t:
        return ""
    # 去掉 "10_概念·" 这类前缀，只留概念名
    t = _re.sub(r"^\d+_", "", t)
    t = _re.sub(r"^概念[·•]?", "", t)
    t = _re.sub(r"\s+", "", t)
    return t


@router.post("/knowledge/lookup")
def lookup_knowledge(body: dict, user: dict = Depends(current_user)):
    """按知识点标签在知识库「概念卡」中精确匹配，只有真命中才返回 doc_id。
    body: {topic: str}
    返回: {doc_id: int|None, name: str|None, matched: bool}
    """
    topic = (body.get("topic") or "").strip()
    if not topic:
        return {"doc_id": None, "matched": False}
    norm_topic = _norm_concept(topic)
    if len(norm_topic) < 2:
        return {"doc_id": None, "matched": False}
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name FROM documents WHERE status='ready' AND name LIKE '%概念%'"
        ).fetchall()
    best = None
    best_score = 0.0
    for r in rows:
        name = r["name"] or ""
        norm_name = _norm_concept(name)
        if not norm_name:
            continue
        # 命中规则：概念名是知识点的子串，或知识点是概念名的子串，或 SequenceMatcher ≥ 0.6
        if norm_name in norm_topic or norm_topic in norm_name:
            score = 0.9
        else:
            score = _difflib.SequenceMatcher(None, norm_topic, norm_name).ratio()
        if score > best_score:
            best_score = score
            best = (r["id"], name)
    # 阈值：低于 0.6 视为知识库里没有这个概念
    if not best or best_score < 0.6:
        return {"doc_id": None, "matched": False, "best_score": round(best_score, 3)}
    return {"doc_id": best[0], "name": best[1], "matched": True, "score": round(best_score, 3)}


@router.post("/knowledge/explain")
def explain_knowledge(body: dict, user: dict = Depends(current_user)):
    """知识库里没有对应概念卡时，用 DeepSeek 即时生成一段通俗解释。
    body: {topic: str}
    返回: {explain: str, source: "llm"}
    """
    topic = (body.get("topic") or "").strip()
    if not topic:
        return {"explain": "", "source": "llm"}
    from .llm import chat_completion
    prompt = (
        f"你是一名大学数学助教。请用本科生能听懂的话，用 150~250 字通俗解释「{topic}」："
        f"它是什么、怎么用、典型题型。不要堆复杂公式，必要时用一行简单公式即可。"
        f"直接输出解释正文，不要标题。"
    )
    try:
        text = chat_completion([{"role": "user", "content": prompt}])
        return {"explain": (text or "").strip(), "source": "llm"}
    except Exception as e:
        return {"explain": f"（LLM 解释生成失败：{e}）", "source": "llm"}


# ---------- 相似习题检测：上传后在习题库中查重，避免重复存入 ----------
import difflib
import re as _re


def _norm_stem(t: str) -> str:
    """归一化题面：去掉空白、标点、LaTeX 命令差异，便于做相似度比较。"""
    if not t:
        return ""
    t = _re.sub(r"\s+", "", t)
    t = _re.sub(r"[，,。.；;、：:？?!！（）()\[\]{}《》<>\"'`]", "", t)
    t = t.replace("\\", "").replace("{", "").replace("}", "").replace("^", "").replace("_", "")
    return t


@router.post("/similar")
def find_similar(body: dict, user: dict = Depends(current_user)):
    """根据题面文本在已存习题库中检索相似题，top_k 条，按相似度降序。
    body: {stem: str, top_k: int=5}
    返回: {items: [{id, qno, source_name, question, difficulty, similarity}]}
    """
    stem = (body.get("stem") or "").strip()
    top_k = int(body.get("top_k") or 5)
    if not stem:
        return {"items": []}
    norm_q = _norm_stem(stem)
    if len(norm_q) < 6:
        return {"items": []}
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT i.id, i.qno, i.question, i.difficulty, i.source_id, s.name AS source_name "
            "FROM exercise_items i JOIN exercise_sources s ON i.source_id=s.id "
            "WHERE i.user_id=? AND i.status='done'",
            (user.get("uid"),),
        ).fetchall()
    scored = []
    for r in rows:
        norm_i = _norm_stem(r["question"] or "")
        if len(norm_i) < 6:
            continue
        # 字符重合度 + SequenceMatcher 混合，对公式/题面改写更鲁棒
        ratio = difflib.SequenceMatcher(None, norm_q, norm_i).ratio()
        if ratio >= 0.45:
            scored.append((ratio, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    items = [
        {
            "id": r["id"],
            "qno": r["qno"],
            "source_name": r["source_name"],
            "question": r["question"],
            "difficulty": r["difficulty"],
            "similarity": round(ratio, 3),
        }
        for ratio, r in scored[:top_k]
    ]
    return {"items": items, "query_norm_len": len(norm_q)}

