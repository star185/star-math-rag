# -*- coding: utf-8 -*-
"""从数学知识库语料（数学_RAG）生成 100 题测试问题集。
含期望来源、参考答案要点、做题体系扩展字段（difficulty/mode/options/answer_index/calc_steps/module）。

用法：
  cd backend
  .venv/Scripts/python.exe scripts/gen_test_questions.py
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

KB = Path(r"C:\word\知识库\知识库\数学_RAG")
OUT = Path(__file__).resolve().parent.parent / "data" / "test_questions.json"
OUT.parent.mkdir(parents=True, exist_ok=True)

random.seed(20261014)  # 可复现


def parse_md(path: Path):
    text = path.read_text(encoding="utf-8")
    meta = {}
    body = text
    if text.startswith("---"):
        m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
        if m:
            raw, body = m.group(1), text[m.end():]
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
                    val = km.group(2).strip()
                    meta[cur] = [] if val == "" else val.strip("\"'")
    return meta, body


def card_sections(body: str) -> dict[str, str]:
    """按卡片字段（**定义** / **直觉** / **关键点** / **易错** / **反例** / **核心公式** 等）切分。

    兼容两种写法：字段与内容同行（**定义**：xxx）与字段独立行（**关键点**：\n- xxx）。
    """
    out: dict[str, str] = {}
    pat = re.compile(r"\*\*([^*\n]+)\*\*\s*[:：]?\s*")
    matches = list(pat.finditer(body))
    if not matches:
        return out
    for i, m in enumerate(matches):
        name = m.group(1).strip()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        content = body[m.end():end].strip()
        content = re.sub(r"^\n+", "", content).strip()
        if content:
            out[name] = content
    return out


def first_points(content: str, n=4, max_len=120):
    if not content:
        return []
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    pts = []
    for ln in lines:
        if ln.startswith("#") or ln.startswith("|") or ln.startswith(">"):
            continue
        s = re.sub(r"^[-*>#|]+", "", ln).strip()
        s = re.sub(r"^（?\d+）?[、.]?\s*", "", s)
        s = s.strip(" ;；。")
        if 6 <= len(s) <= max_len:
            pts.append(s)
        if len(pts) >= n:
            break
    if not pts:
        # 直接取内容前若干字符
        flat = re.sub(r"\s+", " ", content).strip()
        if flat:
            pts.append(flat[:max_len])
    return pts


def field_text(fields: dict, *names: str) -> str:
    for n in names:
        for k, v in fields.items():
            if n in k:
                return v
    return ""


def clean_q(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


Q_TEMPLATES = {
    "概念型": ["什么是{t}？", "如何理解{t}？", "请简述{t}的基本含义。"],
    "简答型": ["简述{t}的主要内容。", "{t}包括哪些要点？", "为什么说{t}很重要？"],
    "综合型": ["请结合相关理论，说明{t}的意义。", "如何把握{t}？"],
}


def build_questions():
    questions: list[dict] = []
    paths = [p for p in sorted(KB.rglob("*.md")) if not p.name.startswith("00_")]
    # 载入全部卡片（frontmatter + 字段）
    cards = []
    for p in paths:
        meta, body = parse_md(p)
        part = str(meta.get("part", "") or "分析")
        title = str(meta.get("title", "") or re.sub(r"^\d+_[^·]+·", "", p.stem))
        fields = card_sections(body)
        cards.append({
            "file": str(p.relative_to(KB)).replace("\\", "/"),
            "part": part,
            "title": title,
            "fields": fields,
            "def_pts": first_points(field_text(fields, "定义") or field_text(fields, "正文"), 3),
            "intuition": first_points(field_text(fields, "直觉"), 2),
            "key_pts": first_points(field_text(fields, "关键点"), 4),
            "mistake": first_points(field_text(fields, "易错"), 3),
            "formula": first_points(field_text(fields, "核心公式"), 3),
        })
    if not cards:
        raise SystemExit(f"语料为空: {KB}")

    qid = 0

    def add(**kw):
        nonlocal qid
        qid += 1
        q = {"id": qid}
        q.update(kw)
        questions.append(q)
        return q

    # ---- 自动生成三类题（保证每张卡有概念题、关键卡有简答/综合） ----
    # 1) 概念型 short：每张卡 1 道（取前 20 张按模块轮换，覆盖三模块）
    concept_pool = [c for c in cards if c["def_pts"]]
    random.shuffle(concept_pool)
    for c in concept_pool[:20]:
        core = clean_q(c["title"])
        add(
            type="概念型", course=c["part"], module=c["part"], difficulty="L1", mode="short",
            question=Q_TEMPLATES["概念型"][0].format(t=core),
            answer_points=c["def_pts"][:3] or [core],
            expected_source={"file": c["file"], "title": c["title"], "section": "定义", "pages": "", "book": ""},
            keywords=[core],
        )

    # 2) 概念型 choice：14 道（正确项 = 本卡要点，干扰项 = 同模块其他卡要点）
    choice_pool = [c for c in cards if c["def_pts"]]
    random.shuffle(choice_pool)
    for c in choice_pool[:14]:
        core = clean_q(c["title"])
        correct = c["def_pts"][0]
        same_mod = [d["def_pts"][0] for d in cards if d["def_pts"] and d["part"] == c["part"] and d["def_pts"][0] != correct]
        others = [d["def_pts"][0] for d in cards if d["def_pts"] and d["part"] != c["part"] and d["def_pts"][0] != correct]
        random.shuffle(same_mod)
        random.shuffle(others)
        distractors = same_mod[:2] + others[:1]
        while len(distractors) < 3:
            distractors.append("以上均不正确")
        options = [correct] + [d[:50] for d in distractors[:3]]
        random.shuffle(options)
        ans = options.index(correct)
        add(
            type="概念型", course=c["part"], module=c["part"], difficulty="L1", mode="choice",
            question=f"关于「{core}」，以下哪项描述最准确？",
            options=options, answer_index=ans,
            answer_points=[correct],
            expected_source={"file": c["file"], "title": c["title"], "section": "定义", "pages": "", "book": ""},
            keywords=[core],
        )

    # 3) 简答型 33：从关键点生成
    short_pool = [c for c in cards if c["key_pts"] or c["def_pts"]]
    random.shuffle(short_pool)
    for c in short_pool[:33]:
        core = clean_q(c["title"])
        pts = (c["key_pts"] or c["def_pts"])[:3]
        section = "关键点" if c["key_pts"] else "定义"
        add(
            type="简答型", course=c["part"], module=c["part"], difficulty="L2", mode="short",
            question=Q_TEMPLATES["简答型"][0].format(t=core),
            answer_points=pts or [core],
            expected_source={"file": c["file"], "title": c["title"], "section": section, "pages": "", "book": ""},
            keywords=[core],
        )

    # 4) 综合型 20：从直觉/关键点生成
    synth_pool = [c for c in cards if c["intuition"] or c["key_pts"]]
    random.shuffle(synth_pool)
    for c in synth_pool[:20]:
        core = clean_q(c["title"])
        pts = (c["intuition"] or c["key_pts"])[:3]
        section = "直觉" if c["intuition"] else "关键点"
        add(
            type="综合型", course=c["part"], module=c["part"], difficulty="L3", mode="short",
            question=Q_TEMPLATES["综合型"][0].format(t=core),
            answer_points=pts or [core],
            expected_source={"file": c["file"], "title": c["title"], "section": section, "pages": "", "book": ""},
            keywords=[core],
        )
    return questions


# ---- 手写高价值题：3 对比型 + 10 计算题（分布三模块） ----
HAND = [
    # 对比型
    {"type": "对比型", "course": "分析", "module": "分析", "difficulty": "L3", "mode": "short",
     "question": "定积分与不定积分的关系是什么？",
     "answer_points": [
         "不定积分是求导的逆运算（全体原函数）",
         "定积分是 Riemann 和的极限（有向面积）",
         "微积分基本定理把两者焊成互逆，但二者并不等价（可积 ≠ 有原函数）",
     ],
     "expected_source": {"file": "10_概念·定积分.md", "title": "定积分", "section": "关键点", "pages": "", "book": ""},
     "keywords": ["定积分", "不定积分", "FTC"]},
    {"type": "对比型", "course": "分析", "module": "分析", "difficulty": "L3", "mode": "short",
     "question": "数列极限与函数极限的关系是什么？",
     "answer_points": [
         "数列极限是下标趋于无穷时项的逼近行为",
         "函数极限是自变量趋于某点/无穷时函数值的逼近行为",
         "Heine 定理把函数极限归结为数列极限（归结原则）",
     ],
     "expected_source": {"file": "10_概念·函数极限.md", "title": "函数极限", "section": "定义", "pages": "", "book": ""},
     "keywords": ["数列极限", "函数极限", "Heine 定理"]},
    {"type": "对比型", "course": "概率统计", "module": "概率统计", "difficulty": "L3", "mode": "short",
     "question": "大数定律与中心极限定理的关系是什么？",
     "answer_points": [
         "大数定律描述样本均值依概率收敛到期望（趋于确定值）",
         "中心极限定理刻画均值波动的分布形状（趋于正态）",
         "二者互为表里：一个讲收敛目标，一个讲收敛的分布形态",
     ],
     "expected_source": {"file": "20_定理·中心极限定理.md", "title": "中心极限定理", "section": "为什么重要", "pages": "", "book": ""},
     "keywords": ["大数定律", "中心极限定理", "正态"]},
    # 计算题（分析 4）
    {"type": "综合型", "course": "分析", "module": "分析", "difficulty": "L3", "mode": "calc",
     "question": "用数列极限的 ε-N 语言证明：lim_{n→∞} 1/n = 0。",
     "calc_steps": [
         "任取 ε>0，要找 N 使 n>N 时 |1/n-0|<ε",
         "即 1/n < ε，等价于 n > 1/ε",
         "取 N = ⌈1/ε⌉（或任意大于 1/ε 的整数），当 n>N 时 |1/n|<ε",
         "由 ε 的任意性，lim 1/n = 0 得证",
     ],
     "answer_points": ["任取 ε>0", "取 N=⌈1/ε⌉", "n>N 时 |1/n-0|<ε", "极限为 0"],
     "expected_source": {"file": "10_概念·数列极限.md", "title": "数列极限", "section": "定义", "pages": "", "book": ""},
     "keywords": ["数列极限", "ε-N 语言"]},
    {"type": "综合型", "course": "分析", "module": "分析", "difficulty": "L3", "mode": "calc",
     "question": "求函数 f(x)=x² 在 x=2 处的导数。",
     "calc_steps": [
         "导数定义：f'(x₀)=lim_{h→0} (f(x₀+h)-f(x₀))/h",
         "代入：((2+h)²-4)/h = (4+4h+h²-4)/h = 4+h",
         "取极限 lim_{h→0}(4+h)=4",
         "f'(2)=4",
     ],
     "answer_points": ["代入导数定义", "差商化简为 4+h", "取极限得 4"],
     "expected_source": {"file": "10_概念·导数.md", "title": "导数", "section": "定义", "pages": "", "book": ""},
     "keywords": ["导数", "切线"]},
    {"type": "综合型", "course": "分析", "module": "分析", "difficulty": "L3", "mode": "calc",
     "question": "计算定积分 ∫₀¹ x dx。",
     "calc_steps": [
         "x 的一个原函数是 x²/2（牛顿-莱布尼茨公式）",
         "代入上下限：x²/2 |₀¹ = 1/2 - 0 = 1/2",
         "∫₀¹ x dx = 1/2",
     ],
     "answer_points": ["原函数 x²/2", "代入上下限", "结果为 1/2"],
     "expected_source": {"file": "10_概念·定积分.md", "title": "定积分", "section": "关键点", "pages": "", "book": ""},
     "keywords": ["定积分", "牛顿-莱布尼茨"]},
    {"type": "综合型", "course": "分析", "module": "分析", "difficulty": "L3", "mode": "calc",
     "question": "判断级数 Σ_{n=1}^{∞} 1/n² 是否收敛（说明依据）。",
     "calc_steps": [
         "通项 1/n² 趋于 0（必要条件满足）",
         "与 p-级数比较：Σ 1/n^p 当 p>1 时收敛",
         "p=2>1，故级数 Σ 1/n² 收敛（且收敛于 π²/6）",
     ],
     "answer_points": ["通项趋于 0", "p-级数 p=2>1 收敛", "结论：收敛"],
     "expected_source": {"file": "20_定理·收敛判别法.md", "title": "收敛判别法", "section": "条件 / 结论", "pages": "", "book": ""},
     "keywords": ["数项级数", "收敛判别法", "p-级数"]},
    # 计算题（线性代数 3）
    {"type": "综合型", "course": "线性代数", "module": "线性代数", "difficulty": "L3", "mode": "calc",
     "question": "计算矩阵乘法 [[1,2],[3,4]] × [[5,6],[7,8]]。",
     "calc_steps": [
         "结果矩阵 C 的 (i,j) 元 = A 第 i 行点乘 B 第 j 列",
         "C₁₁=1·5+2·7=19，C₁₂=1·6+2·8=22",
         "C₂₁=3·5+4·7=43，C₂₂=3·6+4·8=50",
         "结果 = [[19,22],[43,50]]",
     ],
     "answer_points": ["按行乘列规则", "C₁₁=19, C₁₂=22", "C₂₁=43, C₂₂=50"],
     "expected_source": {"file": "10_概念·矩阵.md", "title": "矩阵", "section": "定义", "pages": "", "book": ""},
     "keywords": ["矩阵", "乘法"]},
    {"type": "综合型", "course": "线性代数", "module": "线性代数", "difficulty": "L3", "mode": "calc",
     "question": "计算二阶行列式 |3 4; 2 5|。",
     "calc_steps": [
         "二阶行列式 |a b; c d| = ad - bc",
         "代入：3·5 - 4·2 = 15 - 8 = 7",
         "行列式值为 7",
     ],
     "answer_points": ["ad-bc 公式", "3·5-4·2", "结果为 7"],
     "expected_source": {"file": "10_概念·行列式.md", "title": "行列式", "section": "定义", "pages": "", "book": ""},
     "keywords": ["行列式"]},
    {"type": "综合型", "course": "线性代数", "module": "线性代数", "difficulty": "L3", "mode": "calc",
     "question": "求矩阵 A=[[1,0],[0,2]] 的特征值。",
     "calc_steps": [
         "特征方程 det(A-λI)=0：|1-λ, 0; 0, 2-λ|=0",
         "(1-λ)(2-λ)=0",
         "特征值 λ₁=1, λ₂=2",
     ],
     "answer_points": ["特征方程 det(A-λI)=0", "(1-λ)(2-λ)=0", "λ₁=1, λ₂=2"],
     "expected_source": {"file": "10_概念·特征值.md", "title": "特征值", "section": "定义", "pages": "", "book": ""},
     "keywords": ["特征值", "特征向量"]},
    # 计算题（概率统计 3）
    {"type": "综合型", "course": "概率统计", "module": "概率统计", "difficulty": "L3", "mode": "calc",
     "question": "掷一颗均匀骰子，已知点数为偶数，求点数为 4 的条件概率。",
     "calc_steps": [
         "A={点数为4}，B={点数为偶数}",
         "P(A∩B)=1/6，P(B)=3/6=1/2",
         "P(A|B)=P(A∩B)/P(B)=(1/6)/(1/2)=1/3",
     ],
     "answer_points": ["P(A∩B)=1/6", "P(B)=1/2", "条件概率=1/3"],
     "expected_source": {"file": "10_概念·条件概率.md", "title": "条件概率", "section": "定义", "pages": "", "book": ""},
     "keywords": ["条件概率", "贝叶斯"]},
    {"type": "综合型", "course": "概率统计", "module": "概率统计", "difficulty": "L3", "mode": "calc",
     "question": "设 X 的分布列为 P(X=1)=0.3，P(X=2)=0.5，P(X=3)=0.2，求 X 的期望 E(X)。",
     "calc_steps": [
         "期望定义 E(X)=Σ x·P(X=x)",
         "E(X)=1·0.3 + 2·0.5 + 3·0.2",
         "= 0.3 + 1.0 + 0.6 = 1.9",
     ],
     "answer_points": ["E(X)=Σ x·P(X=x)", "代入分布列", "E(X)=1.9"],
     "expected_source": {"file": "10_概念·期望与方差.md", "title": "期望与方差", "section": "定义", "pages": "", "book": ""},
     "keywords": ["期望", "方差"]},
    {"type": "综合型", "course": "概率统计", "module": "概率统计", "difficulty": "L3", "mode": "calc",
     "question": "某随机变量 X~N(0,1)，利用中心极限定理说明大量独立同分布随机变量之和的近似分布。",
     "calc_steps": [
         "设 X₁,…,Xₙ 独立同分布，均值 μ、方差 σ²",
         "标准化和 Sₙ=(ΣXᵢ-nμ)/(σ√n)",
         "中心极限定理：Sₙ 的分布趋于标准正态 N(0,1)",
         "故 ΣXᵢ 近似服从 N(nμ, nσ²)",
     ],
     "answer_points": ["标准化", "n 足够大时近似正态", "ΣXᵢ ~ N(nμ, nσ²)"],
     "expected_source": {"file": "20_定理·中心极限定理.md", "title": "中心极限定理", "section": "条件 / 结论", "pages": "", "book": ""},
     "keywords": ["中心极限定理", "正态", "大数定律"]},
]


def main():
    auto = build_questions()
    used_q = {h["question"] for h in HAND}
    auto = [q for q in auto if q["question"] not in used_q]

    # 三池均衡：按模块分配自动题，总量凑满 100（含 HAND 13 道）
    target = 100
    hand = list(HAND)
    need = target - len(hand)  # 87
    pools: dict[str, list[dict]] = {"分析": [], "线性代数": [], "概率统计": []}
    for q in auto:
        pools.setdefault(q.get("module") or q.get("course") or "分析", []).append(q)
    order = ["分析", "线性代数", "概率统计"]
    merged: list[dict] = []
    # 按模块轮询取题，保持分布均衡
    pick = {k: 0 for k in order}
    while len(merged) < need:
        progressed = False
        for k in order:
            if len(merged) >= need:
                break
            pool = pools.get(k, [])
            if pick[k] < len(pool):
                merged.append(pool[pick[k]])
                pick[k] += 1
                progressed = True
        if not progressed:
            break
    # 若仍不足，从任意剩余补
    rest = [q for k in order for q in pools.get(k, [])[pick[k]:]]
    for q in rest:
        if len(merged) >= need:
            break
        if q not in merged:
            merged.append(q)
    merged = merged[:need] + hand
    # 汇总
    for i, q in enumerate(merged, 1):
        q["id"] = i
        q.setdefault("module", q.get("course", ""))
        q.setdefault("difficulty", "L2")
        q.setdefault("mode", "short")
        q.setdefault("answer_points", [])
        q.setdefault("keywords", [])

    payload = {
        "name": "Star Math 测试问题集",
        "version": "1.0",
        "size": len(merged),
        "domain": "数学（分析 + 线性代数 + 概率统计）",
        "source_kb": "C:/word/知识库/知识库/数学_RAG",
        "questions": merged,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # CSV
    csv_path = OUT.with_suffix(".csv")
    lines = ["id,type,module,mode,difficulty,question,file,section"]
    for q in merged:
        exp = q.get("expected_source") or {}
        cell = lambda s: '"' + str(s).replace('"', '""') + '"'
        lines.append(",".join([
            str(q["id"]), cell(q.get("type", "")), cell(q.get("module", "")),
            cell(q.get("mode", "")), cell(q.get("difficulty", "")),
            cell(q.get("question", "")), cell(exp.get("file", "")), cell(exp.get("section", "")),
        ]))
    csv_path.write_text("\n".join(lines), encoding="utf-8-sig")

    # Markdown
    md_path = OUT.with_suffix(".md")
    md = [f"# Star Math 测试问题集（{len(merged)} 题）", "",
          f"- 领域：数学（分析 + 线性代数 + 概率统计）",
          f"- 题量：{len(merged)}",
          f"- 用途：检索命中率、答案质量、RAG vs 基线对比实验、做题练习", "",
          "| ID | 类型 | 模块 | 难度 | 问题 | 期望来源 |",
          "|----|------|------|------|------|----------|"]
    for q in merged:
        exp = q.get("expected_source") or {}
        src = f"{exp.get('file', '')} · {exp.get('section', '')}"
        md.append(f"| {q['id']} | {q.get('type','')} | {q.get('module','')} | {q.get('difficulty','')} | {q['question']} | {src} |")
    md_path.write_text("\n".join(md), encoding="utf-8")

    print("WROTE", OUT)
    print("WROTE", csv_path)
    print("WROTE", md_path)
    print("COUNT", len(merged))
    from collections import Counter
    print("TYPES", dict(Counter(q.get("type") for q in merged)))
    print("MODULES", dict(Counter(q.get("module") for q in merged)))
    print("MODES", dict(Counter(q.get("mode") for q in merged)))
    print("DIFF", dict(Counter(q.get("difficulty") for q in merged)))

    # 映射率核验：期望来源 file 是否存在于语料目录
    kb_files = {str(p.relative_to(KB)).replace("\\", "/").lower() for p in KB.rglob("*.md")}
    miss = []
    for q in merged:
        f = ((q.get("expected_source") or {}).get("file") or "").lower()
        if f and f not in kb_files:
            miss.append(f)
    print("MAPPING_MISS:", miss)
    print("MAPPING_RATE:", f"{len(merged)-len(miss)}/{len(merged)}")


if __name__ == "__main__":
    main()
