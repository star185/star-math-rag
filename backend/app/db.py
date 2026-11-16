# -*- coding: utf-8 -*-
"""SQLite 数据访问层。"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  salt TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',
  can_download INTEGER NOT NULL DEFAULT 0,
  email TEXT NOT NULL DEFAULT '',
  must_change_password INTEGER NOT NULL DEFAULT 0,
  token_epoch INTEGER NOT NULL DEFAULT 0,
  invited_by INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS invites (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  note TEXT NOT NULL DEFAULT '',
  max_uses INTEGER NOT NULL DEFAULT 0,
  used_count INTEGER NOT NULL DEFAULT 0,
  expires_at REAL NOT NULL DEFAULT 0,
  disabled INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS password_resets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  code_hash TEXT NOT NULL DEFAULT '',
  salt TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'requested',
  attempts INTEGER NOT NULL DEFAULT 0,
  expires_at REAL NOT NULL DEFAULT 0,
  used_at REAL NOT NULL DEFAULT 0,
  created_by INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_password_resets_user ON password_resets(user_id, status);

CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  path TEXT NOT NULL,
  format TEXT NOT NULL,
  size INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'ready',
  chunk_count INTEGER NOT NULL DEFAULT 0,
  book TEXT DEFAULT '',
  part TEXT DEFAULT '',
  pages TEXT DEFAULT '',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL,
  content TEXT NOT NULL,
  section TEXT DEFAULT '',
  page_hint TEXT DEFAULT '',
  idx INTEGER NOT NULL,
  vector_id TEXT NOT NULL,
  parent_content TEXT DEFAULT '',
  parent_idx INTEGER DEFAULT -1,
  FOREIGN KEY(doc_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  title TEXT NOT NULL DEFAULT '新会话',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id INTEGER NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  sources_json TEXT DEFAULT '[]',
  latency_ms INTEGER DEFAULT 0,
  mode TEXT DEFAULT 'rag',
  created_at REAL NOT NULL,
  FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS eval_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL DEFAULT 0,
  question TEXT NOT NULL,
  mode TEXT NOT NULL,
  params_json TEXT DEFAULT '{}',
  answer TEXT DEFAULT '',
  sources_json TEXT DEFAULT '[]',
  scores_json TEXT DEFAULT '{}',
  latency_ms INTEGER DEFAULT 0,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_usage (
  user_id INTEGER NOT NULL,
  day TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS practice_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  qid INTEGER NOT NULL,
  question TEXT NOT NULL,
  module TEXT DEFAULT '',
  qtype TEXT DEFAULT '',
  difficulty TEXT DEFAULT '',
  mode TEXT DEFAULT 'short',
  answer TEXT DEFAULT '',
  correct INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS exercise_sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  source_type TEXT NOT NULL DEFAULT 'doc',
  path TEXT DEFAULT '',
  format TEXT DEFAULT '',
  size INTEGER NOT NULL DEFAULT 0,
  question_count INTEGER NOT NULL DEFAULT 0,
  questions_json TEXT DEFAULT '[]',
  created_at REAL NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS exercise_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  source_id INTEGER NOT NULL,
  qno INTEGER NOT NULL DEFAULT 0,
  type TEXT NOT NULL DEFAULT 'short',
  difficulty TEXT DEFAULT 'L2',
  question TEXT NOT NULL,
  options_json TEXT DEFAULT '[]',
  answer TEXT DEFAULT '',
  solution TEXT DEFAULT '',
  explanation TEXT DEFAULT '',
  knowledge TEXT DEFAULT '',
  status TEXT DEFAULT 'done',
  created_at REAL NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(source_id) REFERENCES exercise_sources(id) ON DELETE CASCADE
);
"""


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """列不存在才加：吞掉"已存在"，其余异常（锁冲突/磁盘满）照常抛出。"""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column in cols:
        return
    conn.execute(ddl)


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # 兼容旧库：评估记录按用户隔离（P1-8）
        _add_column(conn, "eval_records", "user_id",
                    "ALTER TABLE eval_records ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")
        # 兼容旧库：父子分块新增列（已存在时忽略）
        try:
            conn.execute("ALTER TABLE chunks ADD COLUMN parent_content TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE chunks ADD COLUMN parent_idx INTEGER DEFAULT -1")
        except Exception:
            pass
        # 兼容旧库：习题来源表新增识别题目缓存列
        try:
            conn.execute("ALTER TABLE exercise_sources ADD COLUMN questions_json TEXT DEFAULT '[]'")
        except Exception:
            pass
        # 兼容旧库：找回密码相关列（email 仅预留；token_epoch 用于改密后作废旧令牌）
        for ddl in (
            "ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN token_epoch INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN invited_by INTEGER NOT NULL DEFAULT 0",
        ):
            try:
                conn.execute(ddl)
            except Exception:
                pass


def now() -> float:
    return time.time()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def loads(text: str | None, default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default
