# -*- coding: utf-8 -*-
"""FastAPI 主应用：鉴权、知识库、对话、RAG 问答、评估。"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
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
    DOC_MAX_MB,
    KB_SOURCE_DIR,
    LLM_DAILY_LIMIT,
    LOGIN_BACKOFF_MAX,
    LOGIN_BACKOFF_WINDOW,
    LOGIN_MAX_FAILURES,
    LOGIN_REMEMBER_TTL,
    LOGIN_SHORT_TTL,
    PASSWORD_RESET_COOLDOWN,
    PASSWORD_RESET_MAX_ATTEMPTS,
    PASSWORD_RESET_TTL,
    REGISTER_INVITE_CODE,
    RETRIEVAL_MODE,
    TOP_K,
    UPLOAD_DIR,
)
from .db import dumps, get_conn, init_db, loads, now, row_to_dict
from .deps import can_download, get_current_user, require_admin, require_download
from .errors import fail
from .exercise import router as exercise_router
from .llm import llm_configured
from .logging_setup import setup_logging
from .parsers import parse_document, parse_frontmatter, split_text
from .practice import router as practice_router
from .rag import answer_stream
from .security import (
    create_token,
    generate_reset_code,
    hash_password,
    needs_rehash,
    verify_password,
)
from .vector_store import STORE

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title=APP_NAME, version=APP_VERSION)
app.include_router(practice_router)
app.include_router(exercise_router)

# 无下载权限的用户能看到的原文预览上限（字符）
PREVIEW_CHARS = 4000


# ---------- auth helpers ----------
# get_current_user / require_admin / require_download 统一由 app/deps.py 提供，
# main、exercise、practice 三个模块共用同一实现（含改密后 token_epoch 失效校验）。


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


class ForgotIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)


class ResetIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=4, max_length=32)
    new_password: str = Field(min_length=6, max_length=128)


class ChangePasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


class AdminResetIn(BaseModel):
    new_password: str = ""


class InviteIn(BaseModel):
    code: str = ""
    note: str = ""
    max_uses: int = 0          # 0 = 不限次
    expires_in_days: int = 0   # 0 = 不限期


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
        # 把 .env 中的静态邀请码导入 invites 表作为种子，之后统一在设置页管理
        if REGISTER_INVITE_CODE:
            got = conn.execute("SELECT id FROM invites WHERE code=?", (REGISTER_INVITE_CODE,)).fetchone()
            if not got:
                conn.execute(
                    """INSERT INTO invites(code,note,max_uses,used_count,expires_at,disabled,created_by,created_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (REGISTER_INVITE_CODE, "启动时从 .env 导入（不限次、不限期）", 0, 0, 0, 0, 0, now()),
                )


# ---------- auth api ----------
class _LoginThrottle:
    """登录失败退避：按 username+IP 计数，超阈值后指数等待（单进程内存即可）。"""

    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()
        self._fails: dict[str, list[float]] = {}

    def _recent(self, key: str) -> list[float]:
        cutoff = time.time() - LOGIN_BACKOFF_WINDOW
        ts = [t for t in self._fails.get(key, []) if t > cutoff]
        if ts:
            self._fails[key] = ts
        else:
            self._fails.pop(key, None)
        return ts

    def retry_after(self, key: str) -> int:
        with self._lock:
            ts = self._recent(key)
            if len(ts) < LOGIN_MAX_FAILURES:
                return 0
            delay = min(2 ** (len(ts) - LOGIN_MAX_FAILURES), LOGIN_BACKOFF_MAX)
            wait = int(ts[-1] + delay - time.time())
            return max(wait, 1) if wait > 0 else 0

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._fails.setdefault(key, []).append(time.time())

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


_login_throttle = _LoginThrottle()


def _consume_invite(conn, code: str) -> int | None:
    """校验并消费邀请码，成功返回 invite id（0 表示 .env 静态兜底码），无效返回 None。"""
    code = (code or "").strip()
    if not code:
        return None
    row = conn.execute("SELECT * FROM invites WHERE code=?", (code,)).fetchone()
    if row:
        if row["disabled"]:
            return None
        if float(row["expires_at"] or 0) and float(row["expires_at"]) < now():
            return None
        if int(row["max_uses"] or 0) and int(row["used_count"] or 0) >= int(row["max_uses"]):
            return None
        conn.execute("UPDATE invites SET used_count=used_count+1 WHERE id=?", (row["id"],))
        return int(row["id"])
    if REGISTER_INVITE_CODE and code == REGISTER_INVITE_CODE:
        return 0
    return None


def _invite_required(conn) -> bool:
    """配置了静态码，或 invites 表里还有启用中的码时，注册必须带邀请码。"""
    if REGISTER_INVITE_CODE:
        return True
    row = conn.execute("SELECT COUNT(*) AS c FROM invites WHERE disabled=0").fetchone()
    return bool(row and row["c"])


@app.post("/api/auth/register")
def register(body: RegisterIn):
    with get_conn() as conn:
        if conn.execute("SELECT id FROM users WHERE username=?", (body.username,)).fetchone():
            raise HTTPException(400, "用户名已存在")
        invite_id = 0
        if _invite_required(conn):
            consumed = _consume_invite(conn, body.invite_code)
            if consumed is None:
                raise HTTPException(403, "邀请码无效、已过期或已用尽，请向管理员索取新的邀请码")
            invite_id = consumed
        pwd, salt = hash_password(body.password)
        cur = conn.execute(
            "INSERT INTO users(username,password_hash,salt,role,can_download,invited_by,created_at) VALUES(?,?,?,?,?,?,?)",
            (body.username, pwd, salt, "user", 0, invite_id, now()),
        )
        uid = cur.lastrowid
    token = create_token(uid, body.username, "user", ttl=LOGIN_SHORT_TTL, epoch=0)
    return {
        "token": token,
        "user": {
            "id": uid,
            "username": body.username,
            "role": "user",
            "can_download": False,
            "must_change_password": False,
        },
    }


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request):
    client_ip = (request.client.host if request.client else "") or "-"
    key = f"{(body.username or '').strip().lower()}|{client_ip}"
    wait = _login_throttle.retry_after(key)
    if wait:
        raise fail(429, "E_LOGIN_THROTTLED", f"尝试次数过多，请 {wait} 秒后再试", retry_after=wait)

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (body.username,)).fetchone()
    if not row or not verify_password(body.password, row["password_hash"], row["salt"]):
        _login_throttle.record_failure(key)
        logger.info("登录失败 user=%s ip=%s", body.username, client_ip)
        raise HTTPException(401, "用户名或密码错误")
    _login_throttle.reset(key)
    # 旧库单轮 sha256 的账号，登录成功时顺势升级为 scrypt
    if needs_rehash(row["password_hash"]):
        try:
            new_hash, new_salt = hash_password(body.password)
            with get_conn() as conn:
                conn.execute(
                    "UPDATE users SET password_hash=?, salt=? WHERE id=?",
                    (new_hash, new_salt, row["id"]),
                )
            logger.info("口令哈希已升级为 scrypt user=%s", row["username"])
        except Exception as e:
            logger.warning("口令哈希升级失败 user=%s: %s", row["username"], e)
    # 记住我：长有效期；否则短有效期
    ttl = LOGIN_REMEMBER_TTL if body.remember else LOGIN_SHORT_TTL
    token = create_token(
        row["id"],
        row["username"],
        row["role"],
        ttl=ttl,
        epoch=int(row["token_epoch"] or 0) if "token_epoch" in row.keys() else 0,
    )
    return {
        "token": token,
        "user": {
            "id": row["id"],
            "username": row["username"],
            "role": row["role"],
            "can_download": bool(row["can_download"]) if "can_download" in row.keys() else row["role"] == "admin",
            "must_change_password": bool(row["must_change_password"])
            if "must_change_password" in row.keys()
            else False,
        },
    }


@app.get("/api/me")
def me(user: dict = Depends(get_current_user)):
    return {"user": user}


# ---------- 忘记密码：申请 → 管理员发一次性重置码 → 自助改密 ----------
_FORGOT_HINT = "已提交申请，请向管理员索取一次性重置码（10 分钟内有效）"


@app.post("/api/auth/forgot-password")
def forgot_password(body: ForgotIn):
    # 无论用户名是否存在都返回同一提示，避免用户名枚举
    username = (body.username or "").strip()
    if not username:
        return {"ok": True, "message": _FORGOT_HINT}
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if row:
            uid = row["id"]
            last = conn.execute(
                "SELECT created_at FROM password_resets WHERE user_id=? ORDER BY id DESC LIMIT 1",
                (uid,),
            ).fetchone()
            cooling = bool(last) and now() - float(last["created_at"] or 0) < PASSWORD_RESET_COOLDOWN
            if not cooling:
                # 旧申请作废，避免管理员看到一堆过期请求
                conn.execute(
                    "UPDATE password_resets SET status='cancelled' WHERE user_id=? AND status IN ('requested','issued')",
                    (uid,),
                )
                conn.execute(
                    """INSERT INTO password_resets(user_id,code_hash,salt,status,attempts,expires_at,used_at,created_by,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (uid, "", "", "requested", 0, 0, 0, 0, now()),
                )
    return {"ok": True, "message": _FORGOT_HINT}


@app.post("/api/auth/reset-password")
def reset_password(body: ResetIn):
    username = (body.username or "").strip()
    code = (body.code or "").strip().upper()
    # get_conn 在抛异常时回滚，因此校验失败先记录、出事务后再抛，保证试错计数落库
    error: tuple[int, str] | None = None
    with get_conn() as conn:
        urow = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        rec = None
        if urow:
            rec = conn.execute(
                "SELECT * FROM password_resets WHERE user_id=? AND status='issued' ORDER BY id DESC LIMIT 1",
                (urow["id"],),
            ).fetchone()
        if not urow or not rec:
            error = (400, "重置码无效或已过期，请向管理员重新索取")
        elif float(rec["expires_at"] or 0) < now():
            conn.execute("UPDATE password_resets SET status='expired' WHERE id=?", (rec["id"],))
            error = (400, "重置码已过期，请向管理员重新索取")
        elif int(rec["attempts"] or 0) >= PASSWORD_RESET_MAX_ATTEMPTS:
            conn.execute("UPDATE password_resets SET status='locked' WHERE id=?", (rec["id"],))
            error = (429, "重置码错误次数过多已作废，请向管理员重新索取")
        elif not verify_password(code, rec["code_hash"], rec["salt"]):
            conn.execute("UPDATE password_resets SET attempts=attempts+1 WHERE id=?", (rec["id"],))
            left = PASSWORD_RESET_MAX_ATTEMPTS - int(rec["attempts"] or 0) - 1
            error = (400, f"重置码错误，还可尝试 {max(left, 0)} 次")
        if not error:
            pwd, salt = hash_password(body.new_password)
            conn.execute(
                "UPDATE users SET password_hash=?, salt=?, must_change_password=0, token_epoch=token_epoch+1 WHERE id=?",
                (pwd, salt, urow["id"]),
            )
            conn.execute("UPDATE password_resets SET status='used', used_at=? WHERE id=?", (now(), rec["id"]))
            conn.execute(
                "UPDATE password_resets SET status='cancelled' WHERE user_id=? AND status IN ('requested','issued')",
                (urow["id"],),
            )
    if error:
        raise HTTPException(error[0], error[1])
    return {"ok": True, "username": username}


@app.post("/api/auth/change-password")
def change_password(body: ChangePasswordIn, user: dict = Depends(get_current_user)):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user.get("uid"),)).fetchone()
        ok = bool(row) and verify_password(body.old_password, row["password_hash"], row["salt"])
        new_epoch = int(row["token_epoch"] or 0) if ok else 0
        if ok:
            new_epoch += 1
            pwd, salt = hash_password(body.new_password)
            conn.execute(
                "UPDATE users SET password_hash=?, salt=?, must_change_password=0, token_epoch=? WHERE id=?",
                (pwd, salt, new_epoch, row["id"]),
            )
        payload = (row["id"], row["username"], row["role"]) if row else (0, "", "user")
    if not ok:
        raise HTTPException(400, "当前密码不正确")
    # 原令牌因 epoch 变化已失效，这里补发一枚新令牌保持当前设备登录态
    token = create_token(payload[0], payload[1], payload[2], ttl=LOGIN_SHORT_TTL, epoch=new_epoch)
    return {"ok": True, "token": token}


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


# ---------- 管理员：找回密码处理 ----------
@app.get("/api/admin/password-requests")
def list_password_requests(
    include_done: bool = False,
    user: dict = Depends(require_admin),
):
    """待处理的重置申请（status=requested 未发码 / issued 已发码待使用）。"""
    sql = """
        SELECT r.id, r.user_id, u.username, r.status, r.attempts, r.expires_at, r.created_at,
               CASE WHEN r.code_hash = '' THEN 0 ELSE 1 END AS has_code
          FROM password_resets r JOIN users u ON u.id = r.user_id
         WHERE r.status IN ('requested','issued') OR ? = 1
         ORDER BY r.id DESC LIMIT 50
    """
    with get_conn() as conn:
        rows = conn.execute(sql, (1 if include_done else 0,)).fetchall()
    return {"items": [row_to_dict(r) for r in rows]}


@app.post("/api/admin/users/{user_id}/reset-code")
def admin_issue_reset_code(user_id: int, user: dict = Depends(require_admin)):
    """为指定用户生成一次性重置码（明文仅返回这一次，库里只存哈希）。"""
    code = generate_reset_code()
    code_hash, salt = hash_password(code)
    expires_at = now() + PASSWORD_RESET_TTL
    with get_conn() as conn:
        urow = conn.execute("SELECT id,username FROM users WHERE id=?", (user_id,)).fetchone()
        if not urow:
            raise HTTPException(404, "用户不存在")
        conn.execute(
            "UPDATE password_resets SET status='cancelled' WHERE user_id=? AND status IN ('requested','issued')",
            (user_id,),
        )
        conn.execute(
            """INSERT INTO password_resets(user_id,code_hash,salt,status,attempts,expires_at,used_at,created_by,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (user_id, code_hash, salt, "issued", 0, expires_at, 0, user.get("uid") or 0, now()),
        )
        username = urow["username"]
    return {
        "ok": True,
        "username": username,
        "code": code,
        "expires_in": PASSWORD_RESET_TTL,
        "expires_at": expires_at,
    }


@app.post("/api/admin/users/{user_id}/reset-password")
def admin_reset_password(
    user_id: int,
    body: Optional[AdminResetIn] = None,
    user: dict = Depends(require_admin),
):
    """管理员直接置临时密码；下次登录强制改密。"""
    new_pwd = ((body.new_password if body else "") or "").strip() or generate_reset_code(10)
    pwd, salt = hash_password(new_pwd)
    with get_conn() as conn:
        urow = conn.execute("SELECT id,username FROM users WHERE id=?", (user_id,)).fetchone()
        if not urow:
            raise HTTPException(404, "用户不存在")
        conn.execute(
            "UPDATE users SET password_hash=?, salt=?, must_change_password=1, token_epoch=token_epoch+1 WHERE id=?",
            (pwd, salt, user_id),
        )
        conn.execute(
            "UPDATE password_resets SET status='cancelled' WHERE user_id=? AND status IN ('requested','issued')",
            (user_id,),
        )
        username = urow["username"]
    return {"ok": True, "username": username, "password": new_pwd, "must_change_password": True}


# ---------- 管理员：邀请码 ----------
@app.get("/api/admin/invites")
def list_invites(user: dict = Depends(require_admin)):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT i.id, i.code, i.note, i.max_uses, i.used_count, i.expires_at,
                      i.disabled, i.created_at,
                      COALESCE((SELECT GROUP_CONCAT(u.username, ', ')
                                  FROM users u WHERE u.invited_by = i.id), '') AS users
                 FROM invites i ORDER BY i.id DESC LIMIT 200"""
        ).fetchall()
    items = []
    for r in rows:
        d = row_to_dict(r)
        d["disabled"] = bool(d.get("disabled"))
        d["expired"] = bool(float(d.get("expires_at") or 0)) and float(d["expires_at"]) < now()
        items.append(d)
    return {"items": items}


@app.post("/api/admin/invites")
def create_invite(body: InviteIn, user: dict = Depends(require_admin)):
    code = (body.code or "").strip() or f"SM-{generate_reset_code(4)}-{generate_reset_code(4)}"
    days = max(0, int(body.expires_in_days or 0))
    expires_at = now() + days * 86400 if days else 0
    with get_conn() as conn:
        if conn.execute("SELECT id FROM invites WHERE code=?", (code,)).fetchone():
            raise HTTPException(400, "该邀请码已存在，请换一个或留空自动生成")
        cur = conn.execute(
            """INSERT INTO invites(code,note,max_uses,used_count,expires_at,disabled,created_by,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (code, (body.note or "").strip(), max(0, int(body.max_uses or 0)), 0, expires_at, 0,
             user.get("uid") or 0, now()),
        )
        invite_id = cur.lastrowid
    return {"ok": True, "id": invite_id, "code": code, "expires_at": expires_at}


@app.post("/api/admin/invites/{invite_id}/disable")
def disable_invite(invite_id: int, user: dict = Depends(require_admin)):
    with get_conn() as conn:
        if not conn.execute("SELECT id FROM invites WHERE id=?", (invite_id,)).fetchone():
            raise HTTPException(404, "邀请码不存在")
        conn.execute("UPDATE invites SET disabled=1 WHERE id=?", (invite_id,))
    return {"ok": True, "id": invite_id, "disabled": True}


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
    # 边收边数：超过上限立刻中断，不把超大文件完整写进磁盘
    limit = DOC_MAX_MB * 1024 * 1024
    written = 0
    with dest.open("wb") as f:
        while True:
            chunk = await file.read(65536)
            if not chunk:
                break
            written += len(chunk)
            if written > limit:
                f.close()
                dest.unlink(missing_ok=True)
                raise fail(413, "E_UPLOAD_TOO_LARGE", f"文件过大，上限 {DOC_MAX_MB}MB")
            f.write(chunk)

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
        raise fail(500, "E_INGEST", "入库失败，请检查文件内容后重试", exc=e, doc=filename)


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
    """原文预览：有下载权限返回全文，其余用户只给截断预览。

    「能看不能导出」的边界只落在这里。分块正文属于阅读功能本身（/text 同样返回），
    故不在此处再收窄；该权限降低的是导出成本，不构成保密边界。
    """
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
    truncated = False
    if not can_download(user) and len(content) > PREVIEW_CHARS:
        content = content[:PREVIEW_CHARS]
        truncated = True
    return {
        "id": row["id"],
        "name": row["name"],
        "book": row["book"],
        "part": row["part"],
        "pages": row["pages"],
        "format": row["format"],
        "content": content,
        "truncated": truncated,
        "can_download": can_download(user),
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
            err = "服务异常，请稍后重试"
            logger.exception("问答流式生成中断 user=%s: %s", user.get("uid"), e)
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
            "INSERT INTO eval_records(user_id,question,mode,params_json,answer,sources_json,scores_json,latency_ms,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                user["uid"],
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


def _eval_items(rows) -> list[dict]:
    items = []
    for r in rows:
        d = row_to_dict(r)
        d["sources"] = loads(d.get("sources_json"), [])
        d["scores"] = loads(d.get("scores_json"), {})
        d["params"] = loads(d.get("params_json"), {})
        items.append(d)
    return items


@app.get("/api/eval/list")
def eval_list(limit: int = Query(50, le=200), user: dict = Depends(get_current_user)):
    """只看自己的评测记录；管理员要看全部走 /api/admin/eval/list。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM eval_records WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user["uid"], limit),
        ).fetchall()
    return {"items": _eval_items(rows)}


@app.get("/api/admin/eval/list")
def admin_eval_list(limit: int = Query(100, le=500), user: dict = Depends(require_admin)):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM eval_records ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return {"items": _eval_items(rows)}


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
    """公开健康检查：只回存活与版本，不暴露路径与内部参数。"""
    return {"ok": True, "app": APP_NAME, "version": APP_VERSION}


@app.get("/api/system/info")
def system_info(user: dict = Depends(get_current_user)):
    """需登录的系统信息（设置页展示用）；知识库只回目录名，不回绝对路径。"""
    with get_conn() as conn:
        invite_required = _invite_required(conn)
    return {
        "ok": True,
        "app": APP_NAME,
        "version": APP_VERSION,
        "llm_configured": llm_configured(),
        "vector": STORE.stats(),
        "kb_source": KB_SOURCE_DIR.name,
        "top_k": TOP_K,
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
        "retrieval_mode": RETRIEVAL_MODE,
        "invite_code_required": invite_required,
        "llm_daily_limit": LLM_DAILY_LIMIT,
    }


@app.post("/api/kb/ingest-folder")
def ingest_folder(user: dict = Depends(require_admin)):
    """批量导入知识库源目录（KB_SOURCE_DIR）下的文档。

    不接受路径参数：本接口会把文件路径登记进 documents.path，而 /api/docs/{id}/raw
    可据此读取原文，放开任意路径等于把服务器文件系统变成可读出口。
    """
    root = KB_SOURCE_DIR.resolve()
    if not root.exists():
        raise HTTPException(404, f"知识库目录不存在: {root}")
    files = [p for p in root.rglob("*") if p.suffix.lower() in {".md", ".txt", ".docx", ".pdf"}]
    imported = 0
    skipped = 0
    errors = []
    pending: list[dict] = []
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
            pending.extend(
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
            )
            imported += 1
        except Exception as e:
            errors.append(f"{fp.name}: {e}")
    # 全部文件解析完再一次性写入向量库：避免每篇一次全量重写索引（IO 量 O(N²)）
    if pending:
        try:
            STORE.add(pending)
        except Exception as e:
            errors.append(f"向量入库失败: {e}")
    return {"imported": imported, "skipped": skipped, "errors": errors, "root": str(root)}


# ---------- static frontend ----------
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


@app.get("/api")
def api_index():
    return JSONResponse({"name": APP_NAME, "version": APP_VERSION, "docs": "/docs"})
