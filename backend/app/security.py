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

# 口令哈希算法前缀；库里以 "scrypt$..." 存的按新算法校验，其余按旧单轮 sha256 兼容
HASH_PREFIX = "scrypt$"


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """scrypt 慢哈希（带算法前缀，便于日后升级与旧库兼容）。"""
    salt = salt or secrets.token_hex(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt.encode("utf-8"), n=2**14, r=8, p=1, dklen=32)
    return f"{HASH_PREFIX}{dk.hex()}", salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    if (password_hash or "").startswith(HASH_PREFIX):
        digest, _ = hash_password(password, salt)
        return hmac.compare_digest(digest, password_hash)
    # 旧库：单轮 sha256(salt + password)，登录成功后由调用方升级
    legacy = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return hmac.compare_digest(legacy, password_hash or "")


def needs_rehash(password_hash: str) -> bool:
    return not (password_hash or "").startswith(HASH_PREFIX)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def create_token(
    user_id: int,
    username: str,
    role: str = "user",
    ttl: int | None = None,
    epoch: int = 0,
) -> str:
    ttl = ttl if ttl is not None else TOKEN_TTL_SECONDS
    payload = {
        "uid": user_id,
        "username": username,
        "role": role,
        "ep": int(epoch),
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


# 排除 0/O/1/I 等易混淆字符，便于管理员口头转交
RESET_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_reset_code(length: int = 8) -> str:
    return "".join(secrets.choice(RESET_CODE_ALPHABET) for _ in range(length))
