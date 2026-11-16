# -*- coding: utf-8 -*-
"""付费 LLM 调用的每日配额。

计数入库（llm_usage 表），重启不清零；超限直接 429，不静默降级，
免得用户以为是模型答不出来。
"""
from __future__ import annotations

import logging
import time

from .config import LLM_DAILY_LIMIT
from .db import get_conn
from .errors import fail

logger = logging.getLogger(__name__)


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def remaining(user_id: int, limit: int = LLM_DAILY_LIMIT) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT count FROM llm_usage WHERE user_id=? AND day=?", (user_id, _today())
        ).fetchone()
    used = int(row["count"]) if row else 0
    return max(limit - used, 0)


def consume(user_id: int, cost: int = 1, limit: int = LLM_DAILY_LIMIT) -> None:
    """占用配额；不足时抛 429。先查后加，被拒绝的请求不计数。"""
    cost = max(int(cost), 1)
    day = _today()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT count FROM llm_usage WHERE user_id=? AND day=?", (user_id, day)
        ).fetchone()
        used = int(row["count"]) if row else 0
        if used + cost > limit:
            logger.warning("每日 LLM 配额用尽 user=%s used=%s cost=%s limit=%s", user_id, used, cost, limit)
            raise fail(
                429,
                "E_LLM_QUOTA",
                f"今日 AI 调用次数已用完（每日 {limit} 次），请明天再试或联系管理员",
            )
        if row:
            conn.execute(
                "UPDATE llm_usage SET count=count+? WHERE user_id=? AND day=?", (cost, user_id, day)
            )
        else:
            conn.execute(
                "INSERT INTO llm_usage(user_id,day,count) VALUES(?,?,?)", (user_id, day, cost)
            )
