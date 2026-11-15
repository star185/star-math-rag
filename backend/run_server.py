# -*- coding: utf-8 -*-
"""显式启动脚本：加载向量库并打印诊断信息。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn

from app.vector_store import STORE


def main() -> None:
    print("PY", sys.executable)
    print("META", len(STORE.meta), "VECS", STORE.vectors.shape, "SUM", float(abs(STORE.vectors).sum()))
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
