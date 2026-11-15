# -*- coding: utf-8 -*-
"""Update .env LLM_API_KEY / VISION_LLM_API_KEY with a freshly issued key.
Called by start.bat on every cold start, so 24h gateway expiry never blocks solving.
Usage: python refresh_key.py <new_api_key>
"""
import sys

def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("[refresh_key] no key provided, skip")
        return
    key = sys.argv[1].strip()
    p = ".env"
    lines = open(p, encoding="utf-8").read().splitlines()
    out = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("LLM_API_KEY=") or s.startswith("VISION_LLM_API_KEY="):
            out.append(ln.split("=", 1)[0] + "=" + key)
        else:
            out.append(ln)
    open(p, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print("[refresh_key] .env keys updated")

if __name__ == "__main__":
    main()
