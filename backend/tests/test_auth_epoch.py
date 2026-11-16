# -*- coding: utf-8 -*-
"""鉴权一致性：三处（main/exercise/practice）行为必须相同，改密后旧令牌立即失效。"""
from __future__ import annotations

import hashlib

from app import db as dbm
from app.config import DEFAULT_ADMIN_PASSWORD, LOGIN_MAX_FAILURES
from app.security import create_token, hash_password, verify_password


def _login(client, username: str = "admin", password: str = DEFAULT_ADMIN_PASSWORD) -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def test_stale_epoch_token_rejected_everywhere(client):
    """旧 epoch 的令牌在 exercise 与 practice 上都必须 401（P0-2 回归）。"""
    data = _login(client)
    stale = {"Authorization": "Bearer " + create_token(data["user"]["id"], "admin", "admin", ttl=600, epoch=999)}

    assert client.get("/api/practice/paper?count=1", headers=stale).status_code == 401
    assert client.get("/api/exercises", headers=stale).status_code == 401


def test_valid_token_accepted(client):
    data = _login(client)
    ok = {"Authorization": "Bearer " + data["token"]}
    assert client.get("/api/practice/paper?count=1", headers=ok).status_code == 200
    assert client.get("/api/exercises", headers=ok).status_code == 200


def test_token_of_unknown_user_rejected(client):
    ghost = {"Authorization": "Bearer " + create_token(999999, "ghost", "user", ttl=600, epoch=0)}
    assert client.get("/api/me", headers=ghost).status_code == 401


def test_legacy_hash_is_upgraded_on_login(client):
    """旧单轮 sha256 的账号登录成功后自动升级为 scrypt。"""
    salt = "legacysalt"
    legacy = hashlib.sha256((salt + "legacy-pass").encode("utf-8")).hexdigest()
    with dbm.get_conn() as conn:
        conn.execute(
            "INSERT INTO users(username,password_hash,salt,role,can_download,created_at) VALUES(?,?,?,?,?,?)",
            ("legacy_user", legacy, salt, "user", 0, 0.0),
        )
    _login(client, "legacy_user", "legacy-pass")
    with dbm.get_conn() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE username='legacy_user'").fetchone()
    assert row["password_hash"].startswith("scrypt$")


def test_scrypt_roundtrip():
    digest, salt = hash_password("demo-pass-123")
    assert digest.startswith("scrypt$")
    assert verify_password("demo-pass-123", digest, salt)
    assert not verify_password("wrong-pass", digest, salt)


def test_login_throttled_after_repeated_failures(client):
    """连续失败超过阈值后返回 429（用不存在的用户名，避免影响其他用例的 admin 登录）。"""
    codes = [
        client.post("/api/auth/login", json={"username": "no_such_user", "password": "x"}).status_code
        for _ in range(LOGIN_MAX_FAILURES + 2)
    ]
    assert codes[:LOGIN_MAX_FAILURES] == [401] * LOGIN_MAX_FAILURES
    assert codes[-1] == 429
