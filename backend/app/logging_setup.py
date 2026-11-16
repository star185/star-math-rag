# -*- coding: utf-8 -*-
"""日志初始化：控制台 + storage/app.log（轮转）。

用户侧只看到通用提示，具体异常一律进这里，排查靠它。
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import LOG_FILE, LOG_MAX_BYTES

_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    _configured = True
    root = logging.getLogger()
    if root.handlers:  # 已被外部（uvicorn/pytest）配置过就不重复加
        return
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    # jieba/httpx 之类在 DEBUG 下噪声很大，单独压到 WARNING
    for noisy in ("jieba", "urllib3", "httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=3, encoding="utf-8"
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except Exception:  # 日志目录不可写时不影响主流程
        pass
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(fmt)
    root.addHandler(console)
