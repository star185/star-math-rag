# -*- coding: utf-8 -*-
"""用户可见提示与内部异常的边界。

约定：响应体 detail 只放通用提示（前端原样展示），错误码放 X-Error-Code 响应头，
异常堆栈与上下文写日志。不要在 detail 里拼 str(e)。
"""
from __future__ import annotations

import logging

from fastapi import HTTPException

logger = logging.getLogger(__name__)

GENERIC = "操作失败，请稍后重试"


def fail(
    status: int,
    code: str,
    message: str = GENERIC,
    *,
    exc: Exception | None = None,
    retry_after: int | None = None,
    **ctx,
) -> HTTPException:
    """记录内部异常并返回带错误码的用户可见响应。"""
    if exc is not None:
        logger.exception("请求失败 code=%s ctx=%s: %s", code, ctx or "-", exc)
    else:
        logger.warning("请求失败 code=%s ctx=%s", code, ctx or "-")
    headers = {"X-Error-Code": code}
    if retry_after is not None:
        headers["Retry-After"] = str(int(retry_after))
    return HTTPException(status_code=status, detail=message, headers=headers)
