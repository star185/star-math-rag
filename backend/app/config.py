# -*- coding: utf-8 -*-
"""应用配置。"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """极简 .env 加载（不覆盖已有环境变量）。"""
    if not path.exists():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass


_load_dotenv(BASE_DIR / ".env")
DATA_DIR = BASE_DIR / "data"
STORAGE_DIR = BASE_DIR / "storage"
UPLOAD_DIR = STORAGE_DIR / "uploads"
VECTOR_DIR = STORAGE_DIR / "vectors"
DB_PATH = STORAGE_DIR / "app.db"

for d in (DATA_DIR, STORAGE_DIR, UPLOAD_DIR, VECTOR_DIR, STORAGE_DIR / "exercises"):
    d.mkdir(parents=True, exist_ok=True)

# 知识库源目录（清洗后数学知识库语料）
KB_SOURCE_DIR = Path(os.getenv("KB_SOURCE_DIR", r"C:\word\知识库\知识库\数学_RAG"))

# 安全
# SECRET_KEY：优先取环境变量；未配置或仍为默认开发值时，读取已持久化的随机密钥，
# 缺失则生成并落盘（storage/secret_key.txt），避免固定默认密钥带来的伪造令牌风险。
_SECRET_KEY = os.getenv("SECRET_KEY", "")
if not _SECRET_KEY or _SECRET_KEY == "dev-graduation-rag-secret-change-me":
    _key_file = STORAGE_DIR / "secret_key.txt"
    try:
        if _key_file.exists():
            _SECRET_KEY = _key_file.read_text(encoding="utf-8").strip()
        else:
            import secrets

            _SECRET_KEY = secrets.token_hex(32)
            _key_file.write_text(_SECRET_KEY, encoding="utf-8")
    except Exception:
        import secrets

        _SECRET_KEY = secrets.token_hex(32)
SECRET_KEY = _SECRET_KEY
TOKEN_TTL_SECONDS = int(os.getenv("TOKEN_TTL_SECONDS", str(7 * 24 * 3600)))
DEFAULT_ADMIN_USER = os.getenv("DEFAULT_ADMIN_USER", "admin")
DEFAULT_ADMIN_PASSWORD = os.getenv("DEFAULT_ADMIN_PASSWORD", "admin123")

# 检索
TOP_K = int(os.getenv("TOP_K", "5"))
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "500"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "50"))

# 检索模式与融合（借鉴 RAGFlow 混合检索：向量 + BM25，RRF 融合）
RETRIEVAL_MODE = os.getenv("RETRIEVAL_MODE", "hybrid")  # hybrid / vector / keyword
BM25_K1 = float(os.getenv("BM25_K1", "1.5"))
BM25_B = float(os.getenv("BM25_B", "0.75"))
RRF_K = int(os.getenv("RRF_K", "60"))

# 父子分块（借鉴 RAGFlow Parent-Child Chunking：子块召回、父块送生成）
PARENT_MAX_CHARS = int(os.getenv("PARENT_MAX_CHARS", "1600"))
USE_PARENT_CONTEXT = os.getenv("USE_PARENT_CONTEXT", "1") == "1"

# 登录与注册（借鉴 RAGFlow 用户体系小改进）
LOGIN_SHORT_TTL = int(os.getenv("LOGIN_SHORT_TTL", str(12 * 3600)))            # 不勾选记住我
LOGIN_REMEMBER_TTL = int(os.getenv("LOGIN_REMEMBER_TTL", str(30 * 24 * 3600)))  # 勾选记住我
REGISTER_INVITE_CODE = os.getenv("REGISTER_INVITE_CODE", "")  # 非空则注册必须填邀请码

# LLM（OpenAI 兼容接口；未配置时使用本地抽取式回答）
LLM_API_BASE = os.getenv("LLM_API_BASE", "https://api.deepseek.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2048"))

# Embedding（可选远程；默认本地字符 n-gram）
EMBED_API_BASE = os.getenv("EMBED_API_BASE", "")
EMBED_API_KEY = os.getenv("EMBED_API_KEY", os.getenv("LLM_API_KEY", ""))
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")

# 上传解题 / 习题册（exercise.py）
EXERCISE_UPLOAD_DIR = STORAGE_DIR / "exercises"
EXERCISE_MAX_MB = int(os.getenv("EXERCISE_MAX_MB", "20"))
OCR_ENGINE = os.getenv("OCR_ENGINE", "rapidocr")  # rapidocr / pix2text / none
VISION_LLM_API_BASE = os.getenv("VISION_LLM_API_BASE", "")
VISION_LLM_API_KEY = os.getenv("VISION_LLM_API_KEY", "")
VISION_LLM_MODEL = os.getenv("VISION_LLM_MODEL", "")
SOLVE_TOP_K = int(os.getenv("SOLVE_TOP_K", "5"))
SOLVE_MAX_QUESTIONS = int(os.getenv("SOLVE_MAX_QUESTIONS", "20"))

APP_NAME = "Star Math 智能问答系统"
APP_VERSION = "1.0.0"
