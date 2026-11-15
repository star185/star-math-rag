# -*- coding: utf-8 -*-
"""剔除参考答案生成失败的题，保留干净题目。"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "kmath_questions.json"
data = json.loads(OUT.read_text(encoding="utf-8"))
bad_ids = []
keep = []
for q in data["questions"]:
    pts = q.get("answer_points") or []
    steps = q.get("calc_steps") or ""
    ok = bool(steps.strip()) and bool(pts) and not pts[0].startswith("参考答案生成失败")
    if ok:
        keep.append(q)
    else:
        bad_ids.append(q["id"])
data["questions"] = keep
data["size"] = len(keep)
OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"保留 {len(keep)} 题，剔除 {len(bad_ids)} 题: {bad_ids}")
