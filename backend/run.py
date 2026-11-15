# -*- coding: utf-8 -*-
"""启动入口: python run.py"""
import asyncio
import sys

import uvicorn

if __name__ == "__main__":
    # Windows 下 Proactor 事件循环对 SSE 小帧有写缓冲（整流攒到结束才发出），
    # 改用 Selector 事件循环可逐帧下发；不可用则保持默认。
    if sys.platform == "win32":
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            pass
    # http="h11"：纯 Python 实现，避免 httptools 在 Windows 的流式响应缓冲
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False, http="h11")
