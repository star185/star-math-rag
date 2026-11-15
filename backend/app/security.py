# -*- coding: utf-8 -*-
"""口令哈希与令牌（HMAC 自实现，无第三方依赖）。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time

from .config import SECRET_KEY, TOKEN_TTL_SECONDS


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return digest, salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    digest, _ = hash_password(password, salt)
    return hmac.compare_digest(digest, password_hash)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def create_token(user_id: int, username: str, role: str = "user", ttl: int | None = None) -> str:
    ttl = ttl if ttl is not None else TOKEN_TTL_SECONDS
    payload = {
        "uid": user_id,
        "username": username,
        "role": role,
        "exp": int(time.time()) + ttl,
    }
    raw = _b64url(__import__("json").dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(SECRET_KEY.encode("utf-8"), raw.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def parse_token(token: str) -> dict | None:
    try:
        raw, sig = token.split(".", 1)
        expect = hmac.new(SECRET_KEY.encode("utf-8"), raw.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return None
        import json

        payload = json.loads(_b64url_decode(raw))
        if int(payload.get("exp", 0)) < time.time():
            return None
        return payload
    except Exception:
        return None


def new_id_token() -> str:
    return secrets.token_urlsafe(24)
