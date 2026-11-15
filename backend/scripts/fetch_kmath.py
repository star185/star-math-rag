# -*- coding: utf-8 -*-
"""从 kmath.cn 抓取真实真题（扩量版：多分区、按大知识点源配额）。

接口（公开、无需登录）：
  列表: GET /math/Main_Data.aspx?area=<分区>&qtype=全部&hard=<难度>&orderby=1&pageIndex=<n>
        返回 JSON 数组：id/typesname/hard/question/opta~f/papername/paperid
  官方答案接口需 VIP（"答案VIP会员可见"），故本脚本只抓题干，答案由 LLM 生成。

分区 → 大知识点源：
  dx            考研数学/高等数学（极限、导数、积分、级数、微分方程等综合）
  gaodengdaishu 高等代数（多项式、方程组、线性空间 = 线性代数）
  shuxuefenxi   数学分析（一元积分等 = 积分）

用法: python scripts/fetch_kmath.py [总量] [每分区配额]
输出: backend/data/kmath_raw.json（含 source.area）
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://kmath.cn/math/Main_Data.aspx"
HARDS = ["基础", "中等"]
WANT_TYPES = {"解答题", "证明题", "填空题"}   # 排除单选题
OUT = Path(__file__).resolve().parent.parent / "data" / "kmath_raw.json"

# area: 配额
AREAS = {
    "dx": 40,              # 高数综合（考研数学）
    "gaodengdaishu": 20,   # 高等代数 → 线性代数
    "shuxuefenxi": 15,     # 数学分析 → 积分
}


def fetch_page(area: str, hard: str, page: int) -> list:
    params = {
        "area": area,
        "qtype": "全部",
        "hard": hard,
        "orderby": 1,
        "pageIndex": page,
    }
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    n_default = sum(AREAS.values())
    if len(sys.argv) > 1:
        total = int(sys.argv[1])
    else:
        total = n_default
    if len(sys.argv) > 2:
        # 只抓指定 area（逗号分隔），配额均分到总量
        only = [a.strip() for a in sys.argv[2].split(",") if a.strip()]
    else:
        only = None

    seen: dict[int, dict] = {}
    pages = 0
    for area, quota in AREAS.items():
        if only and area not in only:
            continue
        if len(seen) >= total:
            break
        target = min(quota, total - len(seen))
        for hard in HARDS:
            if len(seen) >= total or sum(1 for x in seen.values() if x.get("area") == area) >= quota:
                break
            for page in range(1, 200):
                got_this = sum(1 for x in seen.values() if x.get("area") == area)
                if got_this >= quota or len(seen) >= total:
                    break
                try:
                    items = fetch_page(area, hard, page)
                except Exception as e:
                    print(f"[warn] {area}/{hard} page {page} 失败: {e}", flush=True)
                    time.sleep(1.0)
                    continue
                if not items:
                    print(f"[info] {area}/{hard} page {page} 空，停止该难度", flush=True)
                    break
                for it in items:
                    if it.get("typesname") in WANT_TYPES and it.get("id") not in seen:
                        seen[it["id"]] = {
                            "id": it["id"],
                            "area": area,
                            "typesname": it.get("typesname", ""),
                            "hard": it.get("hard", ""),
                            "question": it.get("question", ""),
                            "opta": it.get("opta", ""),
                            "optb": it.get("optb", ""),
                            "optc": it.get("optc", ""),
                            "optd": it.get("optd", ""),
                            "papername": it.get("papername", ""),
                            "paperid": it.get("paperid", 0),
                        }
                pages += 1
                time.sleep(0.4)
                print(f"[ok] {area}/{hard} page {page} 累计 {len(seen)}/{total}", flush=True)
            if len(seen) >= total:
                break
    OUT.write_text(json.dumps({"source": "kmath.cn", "count": len(seen),
                               "questions": list(seen.values())}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"完成：抓取 {len(seen)} 题（请求 {pages} 页）→ {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
