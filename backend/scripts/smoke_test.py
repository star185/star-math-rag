# -*- coding: utf-8 -*-
"""一键冒烟测试：健康检查 → 登录 → RAG 问答(SSE) → 检索调试（含噪声查询）→ 测试题集。

用法: python scripts/smoke_test.py [--base http://127.0.0.1:8000]
退出码: 0=全部通过, 1=存在失败项
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error

PASS = []
FAIL = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(("  [PASS] " if ok else "  [FAIL] ") + name + (f" — {detail}" if detail and not ok else ""))


def http_json(method: str, url: str, payload=None, token: str = "", timeout: float = 30):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def chat_sse(base: str, token: str, question: str, timeout: float = 90):
    """POST /api/chat 并解析 SSE，返回 (sources, tokens, done_meta)。"""
    body = json.dumps({"question": question, "top_k": 5, "mode": "rag"}).encode("utf-8")
    req = urllib.request.Request(base + "/api/chat", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    sources, tokens, done = [], [], None
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        buf = b""
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n\n" in buf:
                frame, buf = buf.split(b"\n\n", 1)
                line = next((l for l in frame.decode("utf-8", "replace").splitlines() if l.startswith("data: ")), None)
                if not line:
                    continue
                try:
                    ev = json.loads(line[6:])
                except Exception:
                    continue
                if ev.get("type") == "sources":
                    sources = ev.get("data") or []
                elif ev.get("type") == "token":
                    tokens.append(ev.get("data", ""))
                elif ev.get("type") == "done":
                    done = ev
    return sources, "".join(tokens), done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--username", default="admin")
    ap.add_argument("--password", default="admin123")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    print(f"冒烟测试目标: {base}\n")

    # 1 健康检查
    try:
        h = http_json("GET", base + "/api/health")
        check("健康检查 /api/health", bool(h.get("ok")), f"vector={h.get('vector', {}).get('chunks')} chunks")
    except Exception as e:
        check("健康检查 /api/health", False, str(e))
        return finish()

    # 2 登录
    try:
        login = http_json("POST", base + "/api/auth/login", {"username": args.username, "password": args.password})
        token = login.get("token", "")
        check("登录 admin/admin123", bool(token), "未返回 token" if not token else "")
    except Exception as e:
        check("登录 admin/admin123", False, str(e))
        return finish()

    # 3 错误口令应提示“用户名或密码错误”（P-H1）
    try:
        http_json("POST", base + "/api/auth/login", {"username": args.username, "password": "wrong-pass-1"})
        check("错误口令被拒绝", False, "错误口令竟然登录成功")
    except urllib.error.HTTPError as e:
        detail = json.loads(e.read().decode("utf-8") or "{}").get("detail", "")
        check("错误口令提示后端文案", e.code == 401 and "密码" in str(detail) or "用户名" in str(detail), f"HTTP {e.code}: {detail}")
    except Exception as e:
        check("错误口令被拒绝", False, str(e))

    # 4 空白问题应 422（P-L1）
    try:
        http_json("POST", base + "/api/chat", {"question": "   "}, token=token)
        check("空白问题被拦截", False, "空白问题竟然通过")
    except urllib.error.HTTPError as e:
        check("空白问题被拦截(422)", e.code == 422, f"HTTP {e.code}")
    except Exception as e:
        check("空白问题被拦截", False, str(e))

    # 5 RAG 问答 SSE（数学概念；需 LLM 网关已启动，否则输出“[生成失败…”）
    try:
        sources, text, done = chat_sse(base, token, "简述数列极限的定义")
        failed = "[生成失败" in text or "生成出错" in text or "服务异常" in text or "StopIteration" in text
        ok = len(sources) >= 1 and done is not None and done.get("latency_ms", 0) >= 0 and len(text) > 0 and not failed
        top = sources[0].get("doc_name", "?") if sources else "-"
        check("RAG 问答 SSE（数列极限）", ok, f"来源={len(sources)} top={top} 输出={text[:40]!r}" if not ok else f"来源={len(sources)} top={top}")
    except Exception as e:
        check("RAG 问答 SSE（数列极限）", False, str(e))

    # 6 检索调试：噪声查询应 0 命中（P-M2）
    try:
        d = http_json("POST", base + "/api/search/debug", {"query": "zxcvbnm", "top_k": 5, "mode": "hybrid"}, token=token)
        check("噪声查询 0 命中", len(d.get("items") or []) == 0, f"命中 {len(d.get('items') or [])} 条")
    except Exception as e:
        check("噪声查询 0 命中", False, str(e))

    # 7 检索调试：数学概念应命中（P-M3 知识库修复）
    try:
        d = http_json("POST", base + "/api/search/debug", {"query": "数列极限", "top_k": 5, "mode": "hybrid"}, token=token)
        items = d.get("items") or []
        hit = any("极限" in (i.get("snippet") or "") or "极限" in (i.get("content") or "") for i in items)
        check("数列极限检索命中内容", len(items) >= 1 and hit, f"命中 {len(items)} 条，含极限内容={hit}")
    except Exception as e:
        check("数列极限检索命中内容", False, str(e))

    # 8 测试题集总数 100
    try:
        q = http_json("GET", base + "/api/test-questions?limit=200", token=token)
        check("测试题集 100 题", q.get("total") == 100, f"total={q.get('total')}")
    except Exception as e:
        check("测试题集 100 题", False, str(e))

    return finish()


def finish() -> int:
    print(f"\n结果: {len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项:", "；".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
