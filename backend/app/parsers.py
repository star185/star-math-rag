# -*- coding: utf-8 -*-
"""文档解析与切分（含图片 OCR 与 docx 内嵌图片提取）。"""
from __future__ import annotations

import base64
import json
import re
import urllib.request
from pathlib import Path

from .config import (
    MAX_PDF_PAGES,
    OCR_ENGINE,
    VISION_LLM_API_BASE,
    VISION_LLM_API_KEY,
    VISION_LLM_MODEL,
)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _vision_ocr_image(path: Path) -> str:
    """视觉大模型直接读图转写题面（含公式/上下限，优于通用 OCR）。"""
    if not (VISION_LLM_API_BASE and VISION_LLM_API_KEY and VISION_LLM_MODEL):
        raise RuntimeError("视觉识别未配置（VISION_LLM_API_BASE/KEY/MODEL）")
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    payload = {
        "model": VISION_LLM_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "这是数学题图片。请把题目完整转写为 LaTeX 文本，"
                            "必须保留分段函数、积分上下限、根号、分数等全部信息，不要解答。"
                            "只输出转写结果。"
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
                ],
            }
        ],
        "temperature": 0.1,
        "max_tokens": 1024,
        "stream": False,
    }
    req = urllib.request.Request(
        f"{VISION_LLM_API_BASE.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {VISION_LLM_API_KEY}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = data["choices"][0]["message"]["content"]
    # 去掉“以下是……转写”等包装语，仅保留题面
    text = re.sub(r"^(根据|以下是).{0,30}(转写|内容|题目)[：:。]?\s*", "", text, flags=re.S)
    text = text.strip().strip("`")
    return text


def read_text_file(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except Exception:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def parse_docx(path: Path) -> str:
    """优先用 pandoc 把 docx 转成 markdown（保留 OMML 公式为 LaTeX）；失败时回退 python-docx 纯文本。"""
    import shutil, subprocess
    pandoc = Path(__file__).resolve().parent.parent / "bin" / "pandoc-3.1.11" / "pandoc.exe"
    if not pandoc.exists():
        pandoc = shutil.which("pandoc")
    if pandoc:
        try:
            r = subprocess.run(
                [str(pandoc), str(path), "-f", "docx", "-t", "markdown", "--wrap=none"],
                capture_output=True, timeout=60,
            )
            if r.returncode == 0:
                return r.stdout.decode("utf-8", errors="replace")
        except Exception:
            pass
    # 回退：python-docx 纯文本（公式会丢失）
    import docx
    d = docx.Document(str(path))
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for table in d.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def parse_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        # 页数上限：全量解析上千页 PDF 会长时间占满 CPU 与内存
        if len(reader.pages) > MAX_PDF_PAGES:
            raise RuntimeError(f"PDF 页数 {len(reader.pages)} 超过上限 {MAX_PDF_PAGES}，请拆分后再上传")
        pages = []
        for i, page in enumerate(reader.pages, 1):
            t = page.extract_text() or ""
            if t.strip():
                pages.append(f"[第{i}页]\n{t}")
        return "\n\n".join(pages)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"PDF 解析失败: {e}")


def parse_document(path: Path) -> tuple[str, dict]:
    suffix = path.suffix.lower()
    meta = {"format": suffix.lstrip("."), "name": path.name}
    if suffix in {".md", ".markdown", ".txt"}:
        text = read_text_file(path)
    elif suffix == ".docx":
        text = parse_docx(path)
    elif suffix == ".pdf":
        text = parse_pdf(path)
    else:
        raise ValueError(f"不支持的格式: {suffix}")
    return text, meta


def extract_docx_images(docx_path: Path) -> list[bytes]:
    """提取 docx 内嵌图片的字节内容（用于习题 OCR）。"""
    import docx
    from docx.oxml.ns import qn

    d = docx.Document(str(docx_path))
    out: list[bytes] = []
    for shape in d.inline_shapes:
        try:
            blip = shape._inline.graphic.graphicData.pic.blipFill.blip
            r_id = blip.get(qn("r:embed"))
            if not r_id:
                continue
            part = d.part.related_parts[r_id]
            out.append(part.blob)
        except Exception:
            continue
    return out


def ocr_image(path: Path) -> str:
    """识别图片文字，输出 Markdown 化文本。

    - 配置了 VISION_LLM_MODEL 时优先用视觉大模型读图转写（公式/上下限最准）；
    - 否则按 OCR_ENGINE：rapidocr（默认，模型随包内置，可离线）/ pix2text / none
    """
    # 视觉大模型优先（若配置）
    if VISION_LLM_MODEL:
        try:
            return _vision_ocr_image(path)
        except Exception as e:
            # 视觉模型失败时回退到本地 OCR，不让上传直接报错
            print(f"[parsers] 视觉识别失败，回退本地 OCR: {e}")
    engine = (OCR_ENGINE or "rapidocr").lower()
    if engine == "pix2text":
        try:
            from pix2text import Pix2Text

            p2t = Pix2Text()
            return (p2t.recognize_text(str(path)) or "").strip()
        except Exception as e:
            raise RuntimeError(f"Pix2Text 识别失败（可改用 OCR_ENGINE=rapidocr）: {e}")
    if engine == "none":
        return ""
    # 默认 rapidocr
    try:
        from rapidocr import RapidOCR

        engine_ocr = RapidOCR()
        res = engine_ocr(str(path))
        txts = getattr(res, "txts", None) or []
        if hasattr(res, "to_markdown"):
            md = res.to_markdown()
            if md and md.strip():
                return md.strip()
        lines = [t for t in txts if str(t).strip()]
        return "\n".join(lines)
    except ImportError as e:
        raise RuntimeError(f"RapidOCR 未安装: {e}（请安装 rapidocr 与 onnxruntime）")
    except Exception as e:
        raise RuntimeError(f"OCR 识别失败: {e}")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    if not m:
        return {}, text
    raw, body = m.group(1), text[m.end() :]
    meta: dict = {}
    cur = None
    for line in raw.splitlines():
        if re.match(r"^\s*-\s+", line) and cur:
            meta.setdefault(cur, [])
            if isinstance(meta[cur], list):
                meta[cur].append(re.sub(r"^\s*-\s+", "", line).strip())
            continue
        km = re.match(r"^([A-Za-z_]+):\s*(.*)$", line)
        if km:
            cur = km.group(1)
            val = km.group(2).strip().strip("\"'")
            meta[cur] = [] if val == "" else val
    return meta, body


def strip_markdown_noise(text: str) -> str:
    text = re.sub(r"!?\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), text)
    text = re.sub(r"^\s*>\s*\[!.*?\]\s*", "", text, flags=re.M)
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    return text


def _split_parent_blocks(title: str, body: str, max_chars: int) -> list[dict]:
    """把一个章节切成一个或多个父块（章节语义单元，超长按段落/字符硬切）。"""
    if len(body) <= max_chars:
        return [{"title": title, "content": body}]
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    blocks: list[str] = []
    buf = ""
    for para in paras:
        if len(buf) + len(para) + 1 <= max_chars:
            buf = f"{buf}\n{para}".strip()
        else:
            if buf:
                blocks.append(buf)
            if len(para) > max_chars:
                start = 0
                while start < len(para):
                    blocks.append(para[start : start + max_chars])
                    start += max_chars
                buf = ""
            else:
                buf = para
    if buf:
        blocks.append(buf)
    out = []
    for i, b in enumerate(blocks, 1):
        t = title if len(blocks) == 1 else f"{title}（{i}/{len(blocks)}）"
        out.append({"title": t, "content": b})
    return out


def split_text(
    text: str,
    chunk_size: int = 500,
    overlap: int = 50,
    parent_max_chars: int | None = None,
) -> list[dict]:
    """
    父子两级分块（借鉴 RAGFlow Parent-Child Chunking）：
    先按标题层级切成父块（章节语义单元），再在父块内切成子块（召回单元）。
    每个子块携带 parent_idx / parent_content，检索时子块召回、生成时父块提供完整上下文。
    """
    if parent_max_chars is None:
        from .config import PARENT_MAX_CHARS

        parent_max_chars = PARENT_MAX_CHARS

    text = strip_markdown_noise(text)
    parts = re.split(r"(?m)^(#{1,4}\s+.+)$", text)
    sections: list[tuple[str, str]] = []
    if len(parts) < 2:
        sections = [("", text)]
    else:
        # parts[0] 为前言
        if parts[0].strip():
            sections.append(("", parts[0]))
        i = 1
        while i < len(parts):
            title = re.sub(r"^#+\s+", "", parts[i]).strip()
            content = parts[i + 1] if i + 1 < len(parts) else ""
            sections.append((title, content))
            i += 2

    # 1) 父块：章节级语义单元
    parents: list[dict] = []
    for title, content in sections:
        body = content.strip()
        if not body:
            continue
        parents.extend(_split_parent_blocks(title, body, parent_max_chars))

    # 2) 子块：在每个父块内按段落聚合，超长硬切
    chunks: list[dict] = []
    for pi, parent in enumerate(parents):
        paras = [p.strip() for p in re.split(r"\n\s*\n", parent["content"]) if p.strip()]
        buf = ""
        buf_title = parent["title"]
        for para in paras:
            if len(buf) + len(para) + 1 <= chunk_size:
                buf = f"{buf}\n{para}".strip()
            else:
                if buf:
                    chunks.append(
                        {
                            "content": buf,
                            "section": buf_title,
                            "parent_idx": pi,
                            "parent_content": parent["content"],
                        }
                    )
                # 超长段硬切
                if len(para) > chunk_size:
                    start = 0
                    while start < len(para):
                        piece = para[start : start + chunk_size]
                        chunks.append(
                            {
                                "content": piece,
                                "section": parent["title"],
                                "parent_idx": pi,
                                "parent_content": parent["content"],
                            }
                        )
                        start += max(chunk_size - overlap, 1)
                    buf = ""
                else:
                    buf = para
        if buf:
            chunks.append(
                {
                    "content": buf,
                    "section": buf_title,
                    "parent_idx": pi,
                    "parent_content": parent["content"],
                }
            )

    # 附加 overlap（相邻块前缀）
    if overlap > 0 and len(chunks) > 1:
        out = []
        for i, c in enumerate(chunks):
            prefix = ""
            if i > 0:
                prev = chunks[i - 1]["content"][-overlap:]
                prefix = prev + "\n"
            out.append({**c, "content": (prefix + c["content"]).strip()})
        chunks = out

    # 过滤过短噪声
    chunks = [c for c in chunks if len(c["content"]) >= 20]
    for i, c in enumerate(chunks):
        c["idx"] = i
    return chunks
