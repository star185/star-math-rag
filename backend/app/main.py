# -*- coding: utf-8 -*-
"""FastAPI 主应用：鉴权、知识库、对话、RAG 问答、评估。"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import db as dbm
from .config import (
    APP_NAME,
    APP_VERSION,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    DEFAULT_ADMIN_PASSWORD,
    DEFAULT_ADMIN_USER,
    KB_SOURCE_DIR,
    LOGIN_REMEMBER_TTL,
    LOGIN_SHORT_TTL,
    REGISTER_INVITE_CODE,
    RETRIEVAL_MODE,
    TOP_K,
    UPLOAD_DIR,
)
from .db import dumps, get_conn, init_db, loads, now, row_to_dict
from .exercise import router as exercise_router
from .llm import llm_configured
from .parsers import parse_document, parse_frontmatter, split_text
from .practice import router as practice_router
from .rag import answer_stream
from .security import create_token, hash_password, parse_token, verify_password
from .vector_store import STORE

app = FastAPI(title=APP_NAME, version=APP_VERSION)
app.include_router(practice_router)
app.include_router(exercise_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- auth helpers ----------
def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    payload = parse_token(authorization.split(" ", 1)[1].strip())
    if not payload:
        raise HTTPException(status_code=401, detail="登录已过期")
    # 从库中同步最新权限（便于管理员即时改权）
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


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def require_download(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") == "admin" or user.get("can_download"):
        return user
    raise HTTPException(status_code=403, detail="没有下载权限，请联系管理员开通")


# ---------- schemas ----------
class RegisterIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    invite_code: str = ""


class UserPermIn(BaseModel):
    can_download: Optional[bool] = None
    role: Optional[str] = None


class LoginIn(BaseModel):
    username: str
    password: str
    remember: bool = False


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: Optional[int] = None
    top_k: Optional[int] = None
    mode: str = "rag"
    part: Optional[str] = None
    retrieval_mode: Optional[str] = None

    @field_validator("question")
    @classmethod
    def _question_not_blank(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("问题不能为空")
        return v


class ConvIn(BaseModel):
    title: str = "新会话"


class ConvRenameIn(BaseModel):
    title: str = Field(min_length=1, max_length=80)


class ConvBatchDeleteIn(BaseModel):
    ids: list[int] = Field(min_length=1)


class SearchIn(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = 5
    part: Optional[str] = None
    doc_id: Optional[int] = None
    mode: str = "hybrid"

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("查询内容不能为空")
        return v


class EvalIn(BaseModel):
    question: str
    mode: str = "rag"
    top_k: int = 5
    scores: dict = Field(default_factory=dict)


# ---------- lifecycle ----------
@app.on_event("startup")
def on_startup():
    init_db()
    with get_conn() as conn:
        # 兼容旧库：补 can_download 列
        try:
            conn.execute("ALTER TABLE users ADD COLUMN can_download INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        # 兼容旧库：父子分块新增 parent_content / parent_idx 列（旧数据为空则检索时回退用子块自身）
        try:
            conn.execute("ALTER TABLE chunks ADD COLUMN parent_content TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE chunks ADD COLUMN parent_idx INTEGER DEFAULT -1")
        except Exception:
            pass
        row = conn.execute("SELECT id FROM users WHERE username=?", (DEFAULT_ADMIN_USER,)).fetchone()
        if not row:
            pwd, salt = hash_password(DEFAULT_ADMIN_PASSWORD)
            conn.execute(
                "INSERT INTO users(username,password_hash,salt,role,can_download,created_at) VALUES(?,?,?,?,?,?)",
                (DEFAULT_ADMIN_USER, pwd, salt, "admin", 1, now()),
            )
        else:
            conn.execute("UPDATE users SET role='admin', can_download=1 WHERE username=?", (DEFAULT_ADMIN_USER,))


# ---------- auth api ----------
@app.post("/api/auth/register")
def register(body: RegisterIn):
    # 注册邀请码（借鉴 RAGFlow 用户体系：REGISTER_INVITE_CODE 非空时强制校验）
    if REGISTER_INVITE_CODE and (body.invite_code or "").strip() != REGISTER_INVITE_CODE:
        raise HTTPException(403, "邀请码错误")
    with get_conn() as conn:
        exists = conn.execute("SELECT id FROM users WHERE username=?", (body.username,)).fetchone()
        if exists:
            raise HTTPException(400, "用户名已存在")
        pwd, salt = hash_password(body.password)
        cur = conn.execute(
            "INSERT INTO users(username,password_hash,salt,role,can_download,created_at) VALUES(?,?,?,?,?,?)",
            (body.username, pwd, salt, "user", 0, now()),
        )
        uid = cur.lastrowid
    token = create_token(uid, body.username, "user", ttl=LOGIN_SHORT_TTL)
    return {
        "token": token,
        "user": {"id": uid, "username": body.username, "role": "user", "can_download": False},
    }


@app.post("/api/auth/login")
def login(body: LoginIn):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (body.username,)).fetchone()
    if not row or not verify_password(body.password, row["password_hash"], row["salt"]):
        raise HTTPException(401, "用户名或密码错误")
    # 记住我：长有效期；否则短有效期
    ttl = LOGIN_REMEMBER_TTL if body.remember else LOGIN_SHORT_TTL
    token = create_token(row["id"], row["username"], row["role"], ttl=ttl)
    return {
        "token": token,
        "user": {
            "id": row["id"],
            "username": row["username"],
            "role": row["role"],
            "can_download": bool(row["can_download"]) if "can_download" in row.keys() else row["role"] == "admin",
        },
    }


@app.get("/api/me")
def me(user: dict = Depends(get_current_user)):
    return {"user": user}


# ---------- users / permissions ----------
@app.get("/api/users")
def list_users(user: dict = Depends(require_admin)):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id,username,role,can_download,created_at FROM users ORDER BY id"
        ).fetchall()
    items = []
    for r in rows:
        d = row_to_dict(r)
        d["can_download"] = bool(d.get("can_download"))
        items.append(d)
    return {"items": items}


@app.patch("/api/users/{user_id}/permissions")
def update_user_perm(user_id: int, body: UserPermIn, user: dict = Depends(require_admin)):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "用户不存在")
        if body.role is not None:
            if body.role not in ("admin", "user"):
                raise HTTPException(400, "role 仅支持 admin / user")
            # 禁止移除最后一个 admin
            if body.role != "admin" and row["role"] == "admin":
                admins = conn.execute("SELECT COUNT(*) AS c FROM users WHERE role='admin'").fetchone()
                if admins and admins["c"] <= 1:
                    raise HTTPException(400, "至少保留一个管理员")
            conn.execute("UPDATE users SET role=? WHERE id=?", (body.role, user_id))
        if body.can_download is not None:
            val = 1 if body.can_download else 0
            # 管理员默认可下载
            if row["role"] == "admin" or (body.role == "admin"):
                val = 1
            conn.execute("UPDATE users SET can_download=? WHERE id=?", (val, user_id))
        out = conn.execute(
            "SELECT id,username,role,can_download,created_at FROM users WHERE id=?", (user_id,)
        ).fetchone()
    d = row_to_dict(out)
    d["can_download"] = bool(d.get("can_download"))
    return d


# ---------- docs api ----------
@app.get("/api/docs")
def list_docs(user: dict = Depends(get_current_user)):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM documents ORDER BY updated_at DESC").fetchall()
    return {"items": [row_to_dict(r) for r in rows]}


@app.post("/api/docs/upload")
async def upload_doc(
    file: UploadFile = File(...),
    book: str = Form(""),
    part: str = Form(""),
    pages: str = Form(""),
    user: dict = Depends(require_admin),
):
    filename = Path(file.filename or "upload.bin").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".md", ".markdown", ".txt", ".docx", ".pdf"}:
        raise HTTPException(400, f"不支持格式: {suffix}")
    dest = UPLOAD_DIR / f"{int(time.time()*1000)}_{filename}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        text, meta = parse_document(dest)
        fm, body = parse_frontmatter(text)
        if not (body or "").strip():
            dest.unlink(missing_ok=True)
            raise HTTPException(400, "文件内容为空，无法入库")
        book_v = book or str(fm.get("book", "") or "")
        part_v = part or str(fm.get("part", "") or "")
        pages_v = pages or str(fm.get("pages", "") or "")
        chunks = split_text(body, CHUNK_SIZE, CHUNK_OVERLAP)
        doc_name = str(fm.get("title", "") or Path(filename).stem)
        with get_conn() as conn:
            exists = conn.execute("SELECT id FROM documents WHERE name=?", (doc_name,)).fetchone()
            if exists:
                dest.unlink(missing_ok=True)
                raise HTTPException(409, f"已存在同名文档「{doc_name}」，如需替换请先删除原文档")
            cur = conn.execute(
                """INSERT INTO documents(name,path,format,size,status,chunk_count,book,part,pages,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (doc_name, str(dest), meta.get("format", suffix.lstrip(".")), dest.stat().st_size,
                 "ready", len(chunks), book_v, part_v, pages_v, now(), now()),
            )
            doc_id = cur.lastrowid
            for c in chunks:
                vid = f"{doc_id}-{c['idx']}"
                conn.execute(
                    "INSERT INTO chunks(doc_id,content,section,page_hint,idx,vector_id,parent_content,parent_idx) VALUES(?,?,?,?,?,?,?,?)",
                    (doc_id, c["content"], c.get("section", ""), pages_v, c["idx"], vid, c.get("parent_content", ""), c.get("parent_idx", -1)),
                )
        items = [
            {
                "content": c["content"],
                "doc_id": doc_id,
                "doc_name": doc_name,
                "section": c.get("section", ""),
                "page_hint": pages_v,
                "part": part_v,
                "book": book_v,
                "idx": c["idx"],
                "parent_idx": c.get("parent_idx"),
                "parent_content": c.get("parent_content", ""),
            }
            for c in chunks
        ]
        STORE.add(items)
        return {"id": doc_id, "name": doc_name, "chunks": len(chunks)}
    except HTTPException:
        raise
    except Exception as e:
        # 解析/入库失败时清理已落盘的临时文件，避免磁盘孤儿文件累积
        try:
            dest.unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(500, f"入库失败: {e}")


@app.get("/api/docs/{doc_id}")
def get_doc(doc_id: int, user: dict = Depends(get_current_user)):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not row:
            raise HTTPException(404, "文档不存在")
        chunks = conn.execute(
            "SELECT idx,section,page_hint,substr(content,1,200) AS preview FROM chunks WHERE doc_id=? ORDER BY idx",
            (doc_id,),
        ).fetchall()
    data = row_to_dict(row)
    data["chunks"] = [row_to_dict(c) for c in chunks]
    return data


@app.delete("/api/docs/{doc_id}")
def delete_doc(doc_id: int, user: dict = Depends(require_admin)):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not row:
            raise HTTPException(404, "文档不存在")
        conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        path = row["path"]
    STORE.delete_by_doc(doc_id)
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass
    return {"ok": True}


@app.post("/api/docs/{doc_id}/reindex")
def reindex_doc(doc_id: int, user: dict = Depends(require_admin)):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not row:
            raise HTTPException(404, "文档不存在")
        chunks = conn.execute("SELECT * FROM chunks WHERE doc_id=? ORDER BY idx", (doc_id,)).fetchall()
    STORE.delete_by_doc(doc_id)
    items = [
        {
            "content": c["content"],
            "doc_id": doc_id,
            "doc_name": row["name"],
            "section": c["section"],
            "page_hint": c["page_hint"],
            "part": row["part"],
            "book": row["book"],
            "idx": c["idx"],
            "parent_idx": c["parent_idx"] if "parent_idx" in c.keys() else None,
            "parent_content": c["parent_content"] if "parent_content" in c.keys() else "",
        }
        for c in chunks
    ]
    n = STORE.add(items)
    return {"ok": True, "vectors": n}


@app.get("/api/docs/{doc_id}/download")
def download_doc(doc_id: int, user: dict = Depends(require_download)):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not row:
            raise HTTPException(404, "文档不存在")
        path = Path(row["path"])
        name = row["name"]
    if not path.exists():
        raise HTTPException(404, "源文件不存在")
    # 下载文件名优先用文档名
    suffix = path.suffix or ".txt"
    filename = f"{name}{suffix}" if not name.lower().endswith(suffix.lower()) else name
    return FileResponse(
        str(path),
        media_type="application/octet-stream",
        filename=filename,
    )


@app.get("/api/docs/{doc_id}/text")
def doc_text(doc_id: int, user: dict = Depends(get_current_user)):
    """普通用户可读：返回切分正文（只读预览）。"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not row:
            raise HTTPException(404, "文档不存在")
        chunks = conn.execute(
            "SELECT * FROM chunks WHERE doc_id=? ORDER BY idx", (doc_id,)
        ).fetchall()
    return {
        "id": row["id"],
        "name": row["name"],
        "book": row["book"],
        "part": row["part"],
        "pages": row["pages"],
        "format": row["format"],
        "chunks": [
            {
                "idx": c["idx"],
                "section": c["section"],
                "content": c["content"],
                "parent_idx": c["parent_idx"] if "parent_idx" in c.keys() else -1,
            }
            for c in chunks
        ],
    }


@app.get("/api/docs/{doc_id}/raw")
def doc_raw(doc_id: int, user: dict = Depends(get_current_user)):
    """普通用户可读：返回完整源文件原文（完整 md 预览用），附带分块供切换视图。"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not row:
            raise HTTPException(404, "文档不存在")
        chunks = conn.execute(
            "SELECT * FROM chunks WHERE doc_id=? ORDER BY idx", (doc_id,)
        ).fetchall()
    path = Path(row["path"])
    if not path.exists():
        raise HTTPException(404, "源文件已不存在，无法预览原文")
    fmt = (row["format"] or "").lower()
    if fmt not in {"md", "markdown", "txt"}:
        # docx/pdf 等二进制格式不能按 UTF-8 预览原文，避免乱码，仅提示并保留分块视图
        content = f"【{fmt or '该'}格式不支持原文预览】源文件为二进制格式，请切换到「分块视图」查看解析后的文本内容。"
    else:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = path.read_text(encoding="utf-8", errors="replace")
    return {
        "id": row["id"],
        "name": row["name"],
        "book": row["book"],
        "part": row["part"],
        "pages": row["pages"],
        "format": row["format"],
        "content": content,
        "chunks": [
            {
                "idx": c["idx"],
                "section": c["section"],
                "content": c["content"],
                "parent_idx": c["parent_idx"] if "parent_idx" in c.keys() else -1,
            }
            for c in chunks
        ],
    }


@app.post("/api/search/debug")
def search_debug(body: SearchIn, user: dict = Depends(get_current_user)):
    from .rag import retrieve

    mode = (body.mode or RETRIEVAL_MODE or "hybrid").lower()
    return {
        "mode": mode,
        "items": retrieve(body.query, top_k=body.top_k, part=body.part, doc_id=body.doc_id, mode=mode),
    }


# ---------- conversations ----------
@app.get("/api/conversations")
def list_convs(user: dict = Depends(get_current_user)):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM conversations WHERE user_id=? ORDER BY updated_at DESC",
            (user["uid"],),
        ).fetchall()
    return {"items": [row_to_dict(r) for r in rows]}


@app.post("/api/conversations")
def create_conv(body: ConvIn, user: dict = Depends(get_current_user)):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO conversations(user_id,title,created_at,updated_at) VALUES(?,?,?,?)",
            (user["uid"], body.title, now(), now()),
        )
        cid = cur.lastrowid
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
    return row_to_dict(row)


@app.patch("/api/conversations/{conv_id}")
def rename_conv(conv_id: int, body: ConvRenameIn, user: dict = Depends(get_current_user)):
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(400, "标题不能为空")
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id=? AND user_id=?", (conv_id, user["uid"])).fetchone()
        if not row:
            raise HTTPException(404, "会话不存在")
        conn.execute("UPDATE conversations SET title=?, updated_at=? WHERE id=?", (title, now(), conv_id))
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()
    return row_to_dict(row)


@app.post("/api/conversations/batch-delete")
def batch_delete_convs(body: ConvBatchDeleteIn, user: dict = Depends(get_current_user)):
    deleted = 0
    with get_conn() as conn:
        for cid in body.ids:
            row = conn.execute("SELECT id FROM conversations WHERE id=? AND user_id=?", (cid, user["uid"])).fetchone()
            if not row:
                continue
            conn.execute("DELETE FROM messages WHERE conversation_id=?", (cid,))
            conn.execute("DELETE FROM conversations WHERE id=?", (cid,))
            deleted += 1
    return {"ok": True, "deleted": deleted}


@app.delete("/api/conversations/{conv_id}")
def delete_conv(conv_id: int, user: dict = Depends(get_current_user)):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id=? AND user_id=?", (conv_id, user["uid"])).fetchone()
        if not row:
            raise HTTPException(404, "会话不存在")
        conn.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
        conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
    return {"ok": True}


@app.get("/api/chat/history/{conv_id}")
def history(conv_id: int, user: dict = Depends(get_current_user)):
    with get_conn() as conn:
        conv = conn.execute("SELECT * FROM conversations WHERE id=? AND user_id=?", (conv_id, user["uid"])).fetchone()
        if not conv:
            raise HTTPException(404, "会话不存在")
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at ASC",
            (conv_id,),
        ).fetchall()
    items = []
    for r in rows:
        d = row_to_dict(r)
        d["sources"] = loads(d.get("sources_json"), [])
        items.append(d)
    return {"conversation": row_to_dict(conv), "items": items}


# ---------- chat / RAG ----------
@app.post("/api/chat")
def chat(body: ChatIn, user: dict = Depends(get_current_user)):
    top_k = body.top_k or TOP_K
    # 会话
    with get_conn() as conn:
        if body.conversation_id:
            conv = conn.execute(
                "SELECT * FROM conversations WHERE id=? AND user_id=?",
                (body.conversation_id, user["uid"]),
            ).fetchone()
            if not conv:
                raise HTTPException(404, "会话不存在")
            conv_id = conv["id"]
        else:
            title = body.question[:24] + ("…" if len(body.question) > 24 else "")
            cur = conn.execute(
                "INSERT INTO conversations(user_id,title,created_at,updated_at) VALUES(?,?,?,?)",
                (user["uid"], title, now(), now()),
            )
            conv_id = cur.lastrowid
        hist_rows = conn.execute(
            "SELECT role,content FROM messages WHERE conversation_id=? ORDER BY created_at DESC LIMIT 6",
            (conv_id,),
        ).fetchall()
        history = [dict(r) for r in reversed(hist_rows)]
        conn.execute(
            "INSERT INTO messages(conversation_id,role,content,sources_json,latency_ms,mode,created_at) VALUES(?,?,?,?,?,?,?)",
            (conv_id, "user", body.question, "[]", 0, body.mode, now()),
        )
        conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now(), conv_id))

    async def event_stream():
        tokens: list[str] = []
        sources: list = []
        meta: dict = {}
        yield f"data: {json.dumps({'type': 'session', 'conversation_id': conv_id}, ensure_ascii=False)}\n\n"
        # 同步生成器（含阻塞的检索/LLM 调用）逐帧放进线程执行，
        # 事件循环保持空闲，SSE 每帧可即时下发（避免整体缓冲到流结束）。
        gen = answer_stream(
            body.question,
            history=history,
            top_k=top_k,
            part=body.part,
            mode=body.mode,
            retrieval_mode=body.retrieval_mode,
        )
        def _safe_next(g):
            """在线程内捕获生成器耗尽，避免 StopIteration 跨线程传播
            （asyncio 禁止 StopIteration 传入 Future，会转成 TypeError）。"""
            try:
                return (True, next(g))
            except StopIteration:
                return (False, None)

        try:
            while True:
                ok, ev = await asyncio.to_thread(_safe_next, gen)
                if not ok:
                    break
                if ev["type"] == "token":
                    tokens.append(ev["data"])
                elif ev["type"] == "sources":
                    sources = ev["data"]
                elif ev["type"] == "meta":
                    meta = ev["data"]
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as e:
            err = f"服务异常: {e}"
            tokens.append(err)
            yield f"data: {json.dumps({'type': 'token', 'data': err}, ensure_ascii=False)}\n\n"
        finally:
            answer = "".join(tokens)
            latency = int(meta.get("latency_ms") or 0)
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO messages(conversation_id,role,content,sources_json,latency_ms,mode,created_at) VALUES(?,?,?,?,?,?,?)",
                    (conv_id, "assistant", answer, dumps(sources), latency, body.mode, now()),
                )
            yield f"data: {json.dumps({'type': 'done', 'conversation_id': conv_id, 'latency_ms': latency}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------- eval ----------
@app.post("/api/eval/run")
def eval_run(body: EvalIn, user: dict = Depends(get_current_user)):
    from .rag import answer_complete

    t0 = time.time()
    result = answer_complete(body.question, top_k=body.top_k, mode=body.mode)
    latency = int((time.time() - t0) * 1000)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO eval_records(question,mode,params_json,answer,sources_json,scores_json,latency_ms,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                body.question,
                body.mode,
                dumps({"top_k": body.top_k}),
                result["answer"],
                dumps(result["sources"]),
                dumps(body.scores),
                latency,
                now(),
            ),
        )
    return {"latency_ms": latency, **result}


@app.get("/api/eval/list")
def eval_list(limit: int = Query(50, le=200), user: dict = Depends(get_current_user)):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM eval_records ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    items = []
    for r in rows:
        d = row_to_dict(r)
        d["sources"] = loads(d.get("sources_json"), [])
        d["scores"] = loads(d.get("scores_json"), {})
        d["params"] = loads(d.get("params_json"), {})
        items.append(d)
    return {"items": items}


@app.get("/api/eval/metrics")
def eval_metrics(user: dict = Depends(get_current_user)):
    """返回 eval_retrieval.py 跑出的批量检索指标（vector/keyword/hybrid 对比）。"""
    path = Path(__file__).resolve().parent.parent / "storage" / "eval_retrieval.json"
    if not path.exists():
        return {"available": False, "message": "尚未运行批量检索评测（scripts/eval_retrieval.py）"}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"available": True, **data}


@app.get("/api/test-questions")
def test_questions(limit: int = Query(100, le=200), offset: int = 0, user: dict = Depends(get_current_user)):
    path = Path(__file__).resolve().parent.parent / "data" / "test_questions.json"
    if not path.exists():
        return {"items": [], "total": 0}
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("questions", [])
    return {"total": len(items), "items": items[offset : offset + limit], "meta": {k: data.get(k) for k in ("name", "version", "domain")}}


# ---------- system ----------
@app.get("/api/health")
def health():
    return {
        "ok": True,
        "app": APP_NAME,
        "version": APP_VERSION,
        "llm_configured": llm_configured(),
        "vector": STORE.stats(),
        "kb_source": str(KB_SOURCE_DIR),
        "top_k": TOP_K,
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
        "retrieval_mode": RETRIEVAL_MODE,
        "invite_code_required": bool(REGISTER_INVITE_CODE),
    }


@app.post("/api/kb/ingest-folder")
def ingest_folder(
    path: Optional[str] = None,
    user: dict = Depends(require_admin),
):
    """批量导入文件夹中的 Markdown 知识库。"""
    root = Path(path) if path else KB_SOURCE_DIR
    if not root.exists():
        raise HTTPException(404, f"目录不存在: {root}")
    files = [p for p in root.rglob("*") if p.suffix.lower() in {".md", ".txt", ".docx", ".pdf"}]
    imported = 0
    skipped = 0
    errors = []
    for fp in files:
        try:
            text, meta = parse_document(fp)
            fm, body = parse_frontmatter(text)
            book = str(fm.get("book", "") or "")
            part = str(fm.get("part", "") or "")
            pages = str(fm.get("pages", "") or "")
            name = str(fm.get("title", "") or fp.stem)
            # 去重：同名跳过
            with get_conn() as conn:
                exists = conn.execute("SELECT id FROM documents WHERE name=?", (name,)).fetchone()
                if exists:
                    skipped += 1
                    continue
                chunks = split_text(body, CHUNK_SIZE, CHUNK_OVERLAP)
                cur = conn.execute(
                    """INSERT INTO documents(name,path,format,size,status,chunk_count,book,part,pages,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (name, str(fp), meta.get("format", fp.suffix.lstrip(".")), fp.stat().st_size,
                     "ready", len(chunks), book, part, pages, now(), now()),
                )
                doc_id = cur.lastrowid
                for c in chunks:
                    conn.execute(
                        "INSERT INTO chunks(doc_id,content,section,page_hint,idx,vector_id,parent_content,parent_idx) VALUES(?,?,?,?,?,?,?,?)",
                        (doc_id, c["content"], c.get("section", ""), pages, c["idx"], f"{doc_id}-{c['idx']}",
                         c.get("parent_content", ""), c.get("parent_idx", -1)),
                    )
            STORE.add(
                [
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
                ]
            )
            imported += 1
        except Exception as e:
            errors.append(f"{fp.name}: {e}")
    return {"imported": imported, "skipped": skipped, "errors": errors, "root": str(root)}


# ---------- static frontend ----------
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


@app.get("/api")
def api_index():
    return JSONResponse({"name": APP_NAME, "version": APP_VERSION, "docs": "/docs"})
