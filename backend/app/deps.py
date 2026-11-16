# -*- coding: utf-8 -*-
"""鉴权依赖：全应用唯一实现，供 main / exercise / practice 共用。

把令牌解析、权限同步、改密失效（token_epoch）三件事集中在这里，
避免各模块各写一份后逻辑漂移。
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException

from .db import get_conn
from .security import parse_token


def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    payload = parse_token(authorization.split(" ", 1)[1].strip())
    if not payload:
        raise HTTPException(status_code=401, detail="登录已过期")
    # 从库中同步最新权限（便于管理员即时改权）
    epoch_ok = True
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT id,username,role,can_download,must_change_password,token_epoch FROM users WHERE id=?",
                (payload.get("uid"),),
            ).fetchone()
            if row:
                payload["username"] = row["username"]
                payload["role"] = row["role"]
                payload["can_download"] = bool(row["can_download"])
                payload["must_change_password"] = bool(row["must_change_password"])
                # 改密后 token_epoch 自增，旧令牌在新旧 epoch 不一致时立即失效
                epoch_ok = int(payload.get("ep", 0) or 0) == int(row["token_epoch"] or 0)
            else:
                raise HTTPException(status_code=401, detail="账号不存在，请重新登录")
    except HTTPException:
        raise
    except Exception:
        payload.setdefault("can_download", False)
    if not epoch_ok:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    return payload


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def can_download(user: dict) -> bool:
    return bool(user.get("role") == "admin" or user.get("can_download"))


def require_download(user: dict = Depends(get_current_user)) -> dict:
    if can_download(user):
        return user
    raise HTTPException(status_code=403, detail="没有下载权限，请联系管理员开通")
