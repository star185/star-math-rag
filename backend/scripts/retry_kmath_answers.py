# -*- coding: utf-8 -*-
"""重试失败题：提高 max_tokens 到 1536，只处理 answer_points 为占位的题。"""
import json
import sys
import time
import concurrent.futures
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import llm as llm_mod
from scripts.gen_kmath_questions import extract_json, clean  # noqa: F401

llm_mod.LLM_MAX_TOKENS = 1536

OUT = Path(__file__).resolve().parent.parent / "data" / "kmath_questions.json"


def llm_full(messages):
    parts = []
    for item in llm_mod.stream_chat_completion(messages):
        if isinstance(item, tuple):
            continue
        parts.append(item)
    return "".join(parts)


def retry(q: dict) -> dict:
    prompt = (
        "你是数学解题专家。请解答以下数学题，并提炼得分点。\n\n"
        f"题目：{q['question']}\n"
        f"（来源：{q.get('source', {}).get('papername', '')}）\n\n"
        '只输出一个 JSON 对象（不要代码块围栏、不要任何其他文字）：\n'
        '{"answer": "完整解答过程（使用 LaTeX 公式，逻辑严谨、步骤完整）", '
        '"points": ["得分点1", "得分点2", "得分点3"]}\n\n'
        "要求：\n"
        "- answer 直接给出解题过程，含关键公式推导与结论，公式用 $...$ 包裹，精炼不冗长\n"
        "- points 恰好 3 条，每条一句话\n"
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
    print(f"待重试: {len(todo)} 题", flush=True)
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(retry, q): q for q in todo}
        for fut in concurrent.futures.as_completed(futs):
            q = futs[fut]
            res = fut.result()
            q["answer_points"] = res["points"]
            q["calc_steps"] = res.get("answer", "")
            done += 1
            ok = bool(res.get("answer")) and not res["points"][0].startswith("参考答案生成失败")
            print(f"[{done}/{len(todo)}] #{q['id']} {'OK' if ok else 'FAIL'} pts={len(res['points'])}", flush=True)
    data["size"] = len(data["questions"])
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"完成 → {OUT}", flush=True)


if __name__ == "__main__":
    main()
