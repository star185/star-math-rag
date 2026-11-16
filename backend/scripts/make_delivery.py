# -*- coding: utf-8 -*-
"""生成交付用源码包：排除密钥、本机运行时数据与开发残留。

用法（在 backend 目录下）：
    python scripts/make_delivery.py              # 安全默认：无密钥、无数据库、无向量库
    python scripts/make_delivery.py --with-data  # 连 app.db 与向量库一起打包，便于对方直接演示
    python scripts/make_delivery.py --list       # 只打印将要打包/排除的内容，不写文件
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"

# 任何层级都跳过的目录
SKIP_DIRS = {
    "__pycache__", ".git", ".venv", "venv", "node_modules", "dist",
    ".idea", ".vscode", "bin", "_archive", ".pytest_cache", ".ruff_cache",
    "你的安装目录",  # 本地 npm 缓存目录（已在 .gitignore）
}

# 文件名匹配即排除（顺序无关）
SKIP_FILES = [
    ".env", ".env.*", "*.pyc", "*.pyo", "*.log", "*.bak", "*.bak_*",
    "secret_key.txt", "server.err", "llm_test_out.txt", "*.docx", "*.pdf",
    "_chk*", "_dbg*", "_js_*", "_kmath_inline*", "_probe_out*",
]

# 目录名前缀匹配即整棵排除（开发残留与历史备份）
SKIP_DIR_PREFIXES = ("_chk", "_dbg", "_js_", "frontend_backup_", "storage_backup_")

# 运行时数据：默认排除，--with-data 时才打包
DATA_PATHS = ("backend/storage/app.db", "backend/storage/vectors", "backend/storage/uploads", "backend/storage/exercises")


def _is_data(rel: str) -> bool:
    return any(rel == p or rel.startswith(p + "/") for p in DATA_PATHS)


def _skip_file(rel: str) -> str | None:
    name = rel.rsplit("/", 1)[-1]
    for pat in SKIP_FILES:
        if fnmatch.fnmatch(name, pat):
            return f"匹配规则 {pat}"
    return None


def collect(include_data: bool) -> tuple[list[Path], list[tuple[str, str]]]:
    files: list[Path] = []
    excluded: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel_dir = Path(dirpath).relative_to(ROOT).as_posix()
        keep = []
        for d in sorted(dirnames):
            rel = d if rel_dir == "." else f"{rel_dir}/{d}"
            if d in SKIP_DIRS or d.startswith(SKIP_DIR_PREFIXES):
                excluded.append((rel + "/", f"目录 {d}"))
                continue
            if not include_data and _is_data(rel):
                excluded.append((rel + "/", "运行时数据（--with-data 可包含）"))
                continue
            keep.append(d)
        dirnames[:] = keep
        for f in sorted(filenames):
            rel = f if rel_dir == "." else f"{rel_dir}/{f}"
            if not include_data and _is_data(rel):
                excluded.append((rel, "运行时数据（--with-data 可包含）"))
                continue
            reason = _skip_file(rel)
            if reason:
                excluded.append((rel, reason))
                continue
            files.append(Path(dirpath) / f)
    return files, excluded


def main() -> None:
    ap = argparse.ArgumentParser(description="生成交付源码包（自动排除密钥与本机数据）")
    ap.add_argument("--with-data", action="store_true", help="包含 storage/app.db 与向量库")
    ap.add_argument("--list", action="store_true", help="只打印清单，不生成 zip")
    ap.add_argument("--out", default="", help="输出 zip 路径，默认 dist/star-math-src-<日期>.zip")
    args = ap.parse_args()

    files, excluded = collect(args.with_data)
    total = sum(f.stat().st_size for f in files)
    print(f"待打包 {len(files)} 个文件，共 {total / 1024 / 1024:.2f} MB")
    print(f"已排除 {len(excluded)} 项：")
    for rel, reason in excluded:
        print(f"  - {rel}    ({reason})")

    if args.list:
        print("\n--- 打包清单 ---")
        for f in files:
            print("  " + f.relative_to(ROOT).as_posix())
        return

    out = Path(args.out) if args.out else DIST / f"star-math-src-{dt.date.today():%Y%m%d}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    import zipfile

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, f.relative_to(ROOT).as_posix())
    print(f"\n已生成：{out}  ({out.stat().st_size / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    main()
