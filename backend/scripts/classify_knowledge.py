# -*- coding: utf-8 -*-
"""给原 test_questions.json 100 题批量标注大知识点（8 类白名单，与真题库统一）。"""
from __future__ import annotations

import concurrent.futures
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import llm as llm_mod

llm_mod.LLM_MAX_TOKENS = 600
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "test_questions.json"

KNOWLEDGE_LIST = ["极限与连续", "导数与微分", "一元积分", "多元微分", "级数", "微分方程", "线性代数", "概率统计"]
KNOW_STR = "、".join(KNOWLEDGE_LIST)
BATCH = 10


def llm_full(messages):
    parts = []
    for item in llm_mod.stream_chat_completion(messages):
        if isinstance(item, tuple):
            continue
        parts.append(item)
    return "".join(parts)


def classify_batch(batch: list[dict]) -> dict[int, str]:
    lines = "\n".join(f"{q['id']}. {q['question'][:120]}" for q in batch)
    prompt = (
        "以下每道数学题属于哪个大知识点？只从给定 8 类中选择最匹配的一个。\n\n"
        f"8 类：{KNOW_STR}\n\n"
        f"题目列表：\n{lines}\n\n"
        '只输出一个 JSON 对象（不要代码块围栏、不要其他文字），键为题目 ID，值为知识点：\n'
        '{"<id>": "<知识点>", ...}\n'
    )
    for attempt in range(3):
        try:
            text = llm_full([{"role": "user", "content": prompt}])
            s, e = text.find("{"), text.rfind("}")
            obj = json.loads(text[s:e + 1])
            result = {}
            for q in batch:
                k = str(obj.get(str(q["id"]), "")).strip()
                if k not in KNOWLEDGE_LIST:
                    k = "极限与连续"
                result[q["id"]] = k
            return result
        except Exception:
            time.sleep(1.0)
    return {q["id"]: "极限与连续" for q in batch}


def main():
    force = "--force" in sys.argv
    data = json.loads(OUT.read_text(encoding="utf-8"))
    questions = data["questions"]
    todo = [q for q in questions if force or not q.get("knowledge")]
    if not todo:
        print("全部已标注（加 --force 强制重标）", flush=True)
        return 0
    print(f"待标注 {len(todo)} 题", flush=True)
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(classify_batch, b): b for b in batches}
        done = 0
        for fut in concurrent.futures.as_completed(futs):
            res = fut.result()
            for q in futs[fut]:
                q["knowledge"] = res.get(q["id"], "极限与连续")
            done += len(futs[fut])
            print(f"[{done}/{len(todo)}] 已标注", flush=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"完成：{len(todo)} 题标注（耗时 {time.time()-t0:.0f}s）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
