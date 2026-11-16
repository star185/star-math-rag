# -*- coding: utf-8 -*-
"""大模型调用：OpenAI 兼容 API + 本地抽取式回退。"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request

from .config import (
    LLM_API_BASE,
    LLM_API_KEY,
    LLM_MAX_TOKENS,
    LLM_MODEL,
    LLM_TEMPERATURE,
    LLM_TIMEOUT,
)

# 输出被 max_tokens 截断的标记（生成器内部传递用，不会作为文本下发）
TRUNCATED_MARK = "\x00__TRUNCATED__\x00"

logger = logging.getLogger(__name__)


def llm_configured() -> bool:
    key = (LLM_API_KEY or "").strip()
    if not key or key.startswith("sk-在这里") or "粘贴" in key or key.endswith("密钥"):
        return False
    return len(key) >= 8


def chat_completion(messages: list[dict], stream: bool = False) -> str:
    """非流式完整回复；stream=True 时仍返回完整文本（由生成器分片）。"""
    if not llm_configured():
        raise RuntimeError("LLM_API_KEY 未配置")
    url = f"{LLM_API_BASE.rstrip('/')}/chat/completions"
    body = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": LLM_TEMPERATURE,
        "max_tokens": LLM_MAX_TOKENS,
        "stream": False,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LLM_API_KEY}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def chat_completion_stream(messages: list[dict]):
    """真流式：POST stream=true，逐块 yield 文本片段（SSE 逐行解析）。
    被 max_tokens 截断（finish_reason=length）时，在流末尾额外 yield TRUNCATED_MARK。"""
    if not llm_configured():
        yield extractive_answer_from_messages(messages)
        return
    url = f"{LLM_API_BASE.rstrip('/')}/chat/completions"
    body = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": LLM_TEMPERATURE,
        "max_tokens": LLM_MAX_TOKENS,
        "stream": True,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LLM_API_KEY}",
        },
        method="POST",
    )
    truncated = False
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
        buf = b""
        while True:
            chunk = resp.read(256)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line or not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if payload == b"[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                except Exception:
                    continue
                try:
                    delta = obj["choices"][0].get("delta", {}).get("content") or ""
                except Exception:
                    delta = ""
                if delta:
                    yield delta
                try:
                    if obj["choices"][0].get("finish_reason") == "length":
                        truncated = True
                except Exception:
                    pass
        if truncated:
            yield TRUNCATED_MARK


def stream_chat_completion(messages: list[dict]):
    """Yield 文本片段：LLM 流式输出（mimo 网关），失败时降级为错误提示。
    输出被截断时 yield ("__truncated__", True) 标记（不含实际文本）。"""
    if not llm_configured():
        yield extractive_answer_from_messages(messages)
        return
    try:
        for piece in chat_completion_stream(messages):
            if piece == TRUNCATED_MARK:
                yield ("__truncated__", True)
            else:
                yield piece
    except Exception as e:
        logger.exception("流式生成失败: %s", e)
        yield "\n\n[生成失败，请检查模型配置或稍后重试]"


def extractive_answer_from_messages(messages: list[dict]) -> str:
    """从 prompt 中的参考资料生成抽取式回答（离线可演示）。"""
    user_q = ""
    context = ""
    for m in messages:
        if m.get("role") == "user":
            user_q = m.get("content", "")
        if m.get("role") == "system":
            context = m.get("content", "")
    # 提取【参考资料】块
    cm = re.search(r"【参考资料】\n(.*?)\n【用户问题】", context + "\n【用户问题】\n" + user_q, re.S)
    if not cm:
        cm = re.search(r"【参考资料】\n(.*?)$", context, re.S)
    refs = cm.group(1).strip() if cm else context
    question = re.split(r"【用户问题】", user_q)[-1].strip() if "【用户问题】" in user_q else user_q.strip()

    # 句子打分：与问题字面重叠
    q_chars = set(re.findall(r"[一-鿿A-Za-z0-9]{2,}", question))
    sentences = re.split(r"(?<=[。！？；\n])", refs)
    scored = []
    for sent in sentences:
        s = sent.strip()
        if len(s) < 8:
            continue
        hits = sum(1 for w in q_chars if w in s)
        # 优先带编号的定义句
        bonus = 0.5 if re.match(r"^[（(]?\d+", s) or "是" in s[:12] else 0
        scored.append((hits + bonus, s))
    scored.sort(key=lambda x: -x[0])
    picked = [s for _, s in scored[:5]]
    if not picked:
        picked = [s.strip() for s in sentences if s.strip()][:3]

    lines = [
        "（本地抽取模式）根据知识库检索结果，整理如下要点：",
        "",
    ]
    for i, s in enumerate(picked, 1):
        lines.append(f"{i}. {s.strip()}")
    lines.append("")
    lines.append("说明：当前未配置大模型 API，系统使用检索片段抽取回答。配置 LLM_API_KEY 后可切换为生成式回答。")
    return "\n".join(lines)


def build_messages(
    question: str,
    contexts: list[dict],
    history: list[dict] | None = None,
    mode: str = "rag",
) -> list[dict]:
    # 父子分块：同一父块内的多个子块只保留一条（父块完整上下文），避免重复喂给模型
    blocks = []
    seen: set[tuple] = set()
    for c in contexts:
        if c.get("parent_content"):
            key = (c.get("doc_id"), c.get("parent_idx"))
        else:
            key = (c.get("doc_id"), c.get("idx"))
        if key in seen:
            continue
        seen.add(key)
        blocks.append(c)
    if not blocks:
        blocks = contexts

    ctx_blocks = []
    for i, c in enumerate(blocks, 1):
        title = c.get("doc_name") or c.get("title") or "知识片段"
        section = c.get("section") or ""
        pages = c.get("page_hint") or c.get("pages") or ""
        head = f"[{i}] {title}{' · ' + section if section else ''}{' · p.' + pages if pages else ''}"
        # 生成上下文优先用父块完整内容（父子分块），否则用子块
        body = (c.get("parent_content") or c.get("content") or "").strip()
        if not body:
            body = (c.get("snippet") or "").strip()
        if not body:
            body = "（本条无正文）"
        ctx_blocks.append(f"{head}\n{body}")
    context_text = "\n\n".join(ctx_blocks) if ctx_blocks else "（未检索到相关资料）"
    if mode == "baseline":
        system = (
            "你是数学领域的严谨问答助手。\n"
            "本次为「基线模式」：不提供知识库检索资料，请直接依据你的通用知识作答。\n"
            "1. 正常回答问题，不要因没有参考资料而拒答。\n"
            "2. 概念表述尽量符合教材/规范用语，要点清晰可分点。\n"
            "3. 若确实不确定，在末尾简单说明不确定性。\n"
            "4. 不必标注知识库来源。\n"
            "5. 数学公式必须用 $...$（行内）或 $$...$$（块级）包裹，例如 $\\varepsilon>0$；"
            "比较符号 <、> 前后保留空格（如 $|a_n-L|<\\varepsilon$）；不要输出 &lt;、&gt; 等 HTML 实体。"
        )
    else:
        system = (
            "你是数学领域的严谨问答助手。\n"
            "1. 参考资料是教材/笔记摘录，可能是要点列表或缩进提纲，这些就是正文，必须据此作答，不要因“像提纲”而拒答。\n"
            "2. 仅根据「参考资料」回答，不得编造；若资料确实没有相关内容，再说明「根据现有资料无法确定」。\n"
            "3. 回答用教材规范表述，要点清晰可分点。\n"
            "4. 关键结论句末用方括号标注来源序号（如 [1]），序号对应参考资料编号，便于溯源。\n"
            "5. 文末列出来源，格式：[序号] 文档名 · 章节 · 页码。\n"
            "6. 若问题涉及反例、适用条件或边界情况（如“反例”“例子”“什么时候不成立”“失效”“例外”），"
            "优先引用【参考资料】中的「反例」卡片：先给出正面结论，再说明定理成立的充分条件与失效场景；"
            "若资料无反例内容则如实说明，不要给出无条件的绝对结论。\n"
            "7. 数学公式必须用 $...$（行内）或 $$...$$（块级）包裹，例如 $\\varepsilon>0$；"
            "比较符号 <、> 前后保留空格（如 $|a_n-L|<\\varepsilon$）；不要输出 &lt;、&gt; 等 HTML 实体。\n\n"
            f"【参考资料】\n{context_text}"
        )
    msgs = [{"role": "system", "content": system}]
    if history:
        for h in history[-4:]:
            if h.get("role") in ("user", "assistant") and h.get("content"):
                msgs.append({"role": h["role"], "content": h["content"]})
    msgs.append({"role": "user", "content": f"【用户问题】\n{question}"})
    return msgs
