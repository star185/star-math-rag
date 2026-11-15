# -*- coding: utf-8 -*-
"""失败题最终重试：answer 强制精简（≤120 字，关键结论）。"""
import concurrent.futures
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import llm as llm_mod

llm_mod.LLM_MAX_TOKENS = 900
OUT = Path(__file__).resolve().parent.parent / "data" / "kmath_questions.json"


def llm_full(messages):
    parts = []
    for item in llm_mod.stream_chat_completion(messages):
        if isinstance(item, tuple):
            continue
        parts.append(item)
    return "".join(parts)


def retry(q):
    prompt = (
        "你是数学解题专家。为以下数学题给出精简参考答案。\n\n"
        f"题目：{q['question']}\n\n"
        '只输出一个 JSON 对象（不要代码块围栏、不要其他文字）：\n'
        '{"answer": "精简参考答案：只写关键步骤与最终结论，不超过 120 字，公式用 $...$ 包裹", '
        '"points": ["得分点1", "得分点2", "得分点3"]}\n'
    )
    for attempt in range(3):
        try:
            text = llm_full([{"role": "user", "content": prompt}])
            s, e = text.find("{"), text.rfind("}")
            obj = json.loads(text[s:e + 1])
            answer = str(obj.get("answer", "")).strip()
            points = [str(p).strip() for p in (obj.get("points") or []) if str(p).strip()]
            if answer and points:
                return {"answer": answer, "points": points}
        except Exception:
            time.sleep(1.0)
    return {"answer": "", "points": ["参考答案生成失败，请自行解答"]}


def main():
    data = json.loads(OUT.read_text(encoding="utf-8"))
    todo = [q for q in data["questions"]
            if not (q.get("calc_steps") or "").strip()
            or (q.get("answer_points") or [""])[0].startswith("参考答案生成失败")]
    print(f"待重试: {len(todo)}", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(retry, q): q for q in todo}
        for fut in concurrent.futures.as_completed(futs):
            q = futs[fut]
            res = fut.result()
            q["answer_points"] = res["points"]
            q["calc_steps"] = res.get("answer", "")
            ok = bool(res.get("answer")) and not res["points"][0].startswith("参考答案生成失败")
            print(f"#{q['id']} {'OK' if ok else 'FAIL'} answer_len={len(res.get('answer',''))} pts={len(res['points'])}", flush=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print("完成", flush=True)


if __name__ == "__main__":
    main()
