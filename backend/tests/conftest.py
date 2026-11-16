# -*- coding: utf-8 -*-
"""pytest 公共装置：临时数据库 + 隔离的向量库目录（不碰 storage/）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import db as dbm  # noqa: E402
from app import vector_store as vs  # noqa: E402


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """数据库指向临时文件；get_conn 在调用时读 db.DB_PATH，所以 patch 生效。"""
    db_path = tmp_path / "app.db"
    monkeypatch.setattr(dbm, "DB_PATH", db_path)
    dbm.init_db()
    return db_path


@pytest.fixture
def store(tmp_path, monkeypatch):
    """独立向量库实例：索引读写都落在临时目录。"""
    monkeypatch.setattr(vs, "VECTOR_DIR", tmp_path)
    monkeypatch.setattr(vs, "INDEX_PATH", tmp_path / "index.json")
    monkeypatch.setattr(vs, "VEC_PATH", tmp_path / "vectors.npy")
    return vs.VectorStore()


@pytest.fixture
def client(temp_db, tmp_path, monkeypatch):
    """FastAPI 测试客户端；启动钩子会建默认管理员账号。"""
    monkeypatch.setattr(vs, "VECTOR_DIR", tmp_path)
    monkeypatch.setattr(vs, "INDEX_PATH", tmp_path / "index.json")
    monkeypatch.setattr(vs, "VEC_PATH", tmp_path / "vectors.npy")
    # 模块级单例也清空，避免请求路径读到真实索引
    monkeypatch.setattr(vs.STORE, "meta", [])
    monkeypatch.setattr(vs.STORE, "vectors", vs.STORE._empty_vectors())

    from fastapi.testclient import TestClient

    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as c:
        yield c
