# -*- coding: utf-8 -*-
"""合并两批 kmath_raw → kmath_raw.json（去重）。"""
import json
from pathlib import Path

D = Path(__file__).resolve().parent.parent / "data"
seen = {}
for name in ["kmath_raw_batch1.json", "kmath_raw.json"]:
    p = D / name
    if p.exists():
        for q in json.loads(p.read_text(encoding="utf-8"))["questions"]:
            seen[q["id"]] = q
# 恢复主文件
merged = {"source": "kmath.cn", "count": len(seen), "questions": list(seen.values())}
(D / "kmath_raw.json").write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
# 清理批次文件
(D / "kmath_raw_batch1.json").unlink(missing_ok=True)
from collections import Counter
areas = Counter(q.get("area", "") for q in merged["questions"])
print(f"合并 {len(seen)} 题: {dict(areas)}")
